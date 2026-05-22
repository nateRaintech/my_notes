"""Autodev runner — launches Claude Code in autonomous mode and captures output.

Usage:
    python autodev.py --hours 2            # Run consecutive sessions for 2 hours
    python autodev.py --hours 0.5          # Run for 30 minutes
    python autodev.py                      # Run a single session (default)
    python autodev.py --until-done         # Run until board + roadmap are exhausted (10s between sessions)
    python autodev.py --until-done --spacer 30  # Run until board is clear, 30 min between sessions
    python autodev.py --always             # Like --until-done, but never exit: when the board
                                           #   clears, poll hourly for new work and resume
    python autodev.py --always --spacer 30 --poll-interval 30  # Resume with 30-min spacing,
                                           #   poll every 30 min while idle
    python autodev.py --always --workers 4 # Run forever, 4 parallel build workers per cycle
                                           #   (each in its own git worktree) + 1 integration pass
"""

import argparse
import io
import json
import os
import smtplib
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _force_utf8_stdout() -> None:
    """Wrap stdout in a UTF-8 TextIOWrapper so Unicode chars (→, etc.) don't crash on cp1252 consoles.

    Called from ``main()`` only — at module-import time this would clobber
    pytest's capture machinery (which replaces sys.stdout with a tempfile-backed
    stream that doesn't survive being re-wrapped via ``.buffer``).
    """
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        # No `.buffer` (e.g., already wrapped or running under a harness) — leave stdout alone.
        pass

WORK_DIR = Path(r"C:\Users\Nate\anaconda3\envs\playground\my_notes")
DEVLOGS_DIR = WORK_DIR / "devlogs"
GH_CLI = r"C:\Users\Nate\bin\gh.exe"
PY_EXE = r"C:\Users\Nate\anaconda3\envs\playground\python.exe"
PROJECT_NUMBER = "6"
PROJECT_OWNER = "nateRaintech"
PROJECT_URL = "https://github.com/users/nateRaintech/projects/6"
DEFAULT_BRANCH = "main"  # the repo's default branch — worktrees branch from origin/<this>
ACTIVE_STATUSES = {"Backlog", "Todo", "In Progress", "In Review"}
BUILD_STATUSES = {"Todo", "In Progress"}  # columns a parallel build worker may claim

# How long --always waits between board polls once the board is clear. This is
# only a check for newly-eligible work — it never runs a session — so an hour
# keeps API/log noise low without leaving fresh issues sitting too long.
POLL_INTERVAL_SECONDS = 3600

# Where the runner drops the board snapshot it hands each session (issue #6:
# cheaper cold starts). The session reads this instead of re-querying the board.
# It is regenerated every session and must be git-ignored.
BOARD_SNAPSHOT_FILE = "board_snapshot.md"

# Parent dir for the throwaway git worktrees parallel build workers run in
# (--workers > 1). Created next to the repo so it never pollutes the working tree.
WORKTREES_DIR = WORK_DIR.parent / f"{WORK_DIR.name}-autodev-worktrees"

# Prompts the runner hands Claude. The default drives the full priority loop;
# the other two scope a session for parallel mode (see autodev.md "Execution Modes").
DEFAULT_PROMPT = "follow instructions in autodev.md"
INTEGRATION_PROMPT = (
    "follow instructions in autodev.md — INTEGRATION MODE: this session handles "
    "Priority 0 (fix or revert a broken default branch) and Priority 1 (review and "
    "merge ready PRs) ONLY. Do not start new Todo/In Progress build work."
)
SINGLE_ISSUE_PROMPT = (
    "follow instructions in autodev.md — SINGLE-ISSUE MODE: work ONLY on issue #{number} "
    "in this worktree, as Priority 2/3. Do not review or merge other PRs, do not pick up "
    "any other issue, and do not switch to or merge into the default branch. Create the "
    "feature branch, do the work, push, open a PR, and move the item to In Review."
)


# Single-line GraphQL — shell=True on Windows mangles multi-line string args.
# NOTE: this project's board is owned by a GitHub *user* (nateRaintech), not an
# organization, so the root field is `user(...)`. (The stock template uses
# `organization(...)`; switch back if a project is ever moved under an org.)
_BOARD_GRAPHQL = (
    'query($owner: String!, $number: Int!, $endCursor: String) { '
    'user(login: $owner) { projectV2(number: $number) { '
    'items(first: 100, after: $endCursor) { '
    'pageInfo { endCursor hasNextPage } '
    'nodes { content { ... on Issue { number title } } '
    'fieldValueByName(name: "Status") { '
    '... on ProjectV2ItemFieldSingleSelectValue { name } } } } } } }'
)


def _fetch_all_board_items() -> list[dict]:
    """Fetch every project item via cursor pagination (no server-side status filter).

    ProjectV2's GraphQL API has no status-filter predicate — we have to pull
    every item and inspect `fieldValueByName(name: "Status")` client-side.
    Using `gh api graphql --paginate` walks the cursor until `hasNextPage`
    is false, so this works regardless of board size. Returns a list of
    ``{"number": str, "title": str, "status": str}`` dicts.
    """
    try:
        result = subprocess.run(
            [GH_CLI, "api", "graphql", "--paginate",
             "-F", f"owner={PROJECT_OWNER}",
             "-F", f"number={PROJECT_NUMBER}",
             "-f", f"query={_BOARD_GRAPHQL}"],
            cwd=str(WORK_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        print("Warning: board fetch timed out.")
        return []

    if result.returncode != 0:
        print(f"Warning: board fetch failed (exit {result.returncode})")
        print(f"  stderr: {result.stderr.strip()}")
        return []

    # --paginate concatenates one JSON object per page. Walk the stream and
    # flatten the items from every page into one list.
    items: list[dict] = []
    decoder = json.JSONDecoder()
    text = result.stdout.strip()
    pos = 0
    while pos < len(text):
        # Skip whitespace between pages.
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text):
            break
        try:
            page, end = decoder.raw_decode(text, pos)
        except json.JSONDecodeError as e:
            print(f"Warning: malformed page in board fetch ({e}).")
            break
        pos = end
        nodes = (
            page.get("data", {})
                .get("user", {})
                .get("projectV2", {})
                .get("items", {})
                .get("nodes", [])
        )
        for n in nodes:
            content = n.get("content") or {}
            number = content.get("number")
            if number is None:
                continue  # draft items (no linked issue) — skip
            status_obj = n.get("fieldValueByName") or {}
            items.append({
                "number": str(number),
                "title": content.get("title", ""),
                "status": status_obj.get("name", ""),
            })
    return items


def board_has_active_items() -> bool:
    """Check if the project board has items in Backlog, Todo, In Progress, or In Review.

    Paginates the full board (ProjectV2 has no server-side status filter) and
    returns True if any item's status is in ``ACTIVE_STATUSES``. If the fetch
    fails, returns True so ``--until-done`` errs on the side of continuing
    rather than quitting silently.
    """
    try:
        items = _fetch_all_board_items()
    except Exception as e:  # defensive: never let fetch errors end the loop
        print(f"Warning: board check failed ({e}). Assuming work remains.")
        return True
    if not items:
        # Empty result = either a fetch failure (warning already printed) or a
        # truly clear board. Conservative read: continue.
        return True
    return any(item["status"] in ACTIVE_STATUSES for item in items)


def get_blocked_items() -> list[dict[str, str]]:
    """Return a list of items in the Blocked column with their issue number and title."""
    try:
        items = _fetch_all_board_items()
    except Exception:
        return []
    return [
        {"number": item["number"], "title": item["title"]}
        for item in items
        if item["status"] == "Blocked"
    ]


def send_blocked_alert(blocked_items: list[dict[str, str]]) -> bool:
    """Send an email alert listing all blocked items. Returns True on success."""
    smtp_server = os.environ.get("SMTP_SERVER")
    smtp_port = os.environ.get("SMTP_PORT", "587")
    smtp_user = os.environ.get("SMTP_USERNAME")
    smtp_pass = os.environ.get("SMTP_PASSWORD")
    alert_from = os.environ.get("ALERT_FROM")
    alert_to = os.environ.get("ALERT_TO")

    if not all([smtp_server, smtp_user, smtp_pass, alert_from, alert_to]):
        print("Warning: SMTP not configured — cannot send blocked-items alert.")
        return False

    lines = ["The autodev runner has no actionable items remaining.", "",
             "The following items are in the Blocked column and need human input:", ""]
    for item in blocked_items:
        lines.append(f"  - #{item['number']}: {item['title']}")
    lines.extend(["", f"Review the board: {PROJECT_URL}",
                   "", "Move items out of Blocked once unblocked so autodev can pick them up."])

    msg = MIMEText("\n".join(lines))
    msg["Subject"] = "autodev: all items blocked — human input needed"
    msg["From"] = alert_from
    msg["To"] = alert_to

    try:
        with smtplib.SMTP(smtp_server, int(smtp_port)) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(alert_from, [alert_to], msg.as_string())
        print(f"Blocked-items alert sent to {alert_to}")
        return True
    except (smtplib.SMTPException, OSError) as e:
        print(f"Warning: failed to send blocked-items alert: {e}")
        return False


def _current_head_sha(cwd: Path = WORK_DIR) -> str:
    """Return the current HEAD commit SHA in ``cwd``, or '' if `git rev-parse` fails."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _requirements_changed_since(start_sha: str, cwd: Path = WORK_DIR) -> bool:
    """Return True if `requirements.txt` differs between ``start_sha`` and the working tree in ``cwd``.

    Compares the start SHA to the current working tree (committed + uncommitted),
    so a session that edited requirements.txt but failed to commit still trips
    the install. On any git error, returns False — we'd rather skip a needed
    install than spam pip when git is broken; the session log makes either
    failure visible.
    """
    if not start_sha:
        return False
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", start_sha, "--", "requirements.txt"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    if result.returncode != 0:
        return False
    return bool(result.stdout.strip())


def _pip_install_requirements(cwd: Path = WORK_DIR) -> tuple[bool, str]:
    """Run ``pip install -r requirements.txt`` (from ``cwd``) in the conda env.

    Returns ``(success, combined_output)``. ``success`` is True iff pip exited 0.
    Output combines stdout + stderr so the session log captures both. Times out
    after 10 minutes (pip can be slow on a cold cache); a timeout returns
    ``(False, "<timeout message>")``.
    """
    try:
        result = subprocess.run(
            [PY_EXE, "-m", "pip", "install", "-r", "requirements.txt"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return False, "pip install -r requirements.txt timed out after 600s"
    except OSError as e:
        return False, f"pip install failed to launch: {e}"
    combined = (result.stdout or "") + (result.stderr or "")
    return result.returncode == 0, combined


def install_requirements_if_changed(start_sha: str, cwd: Path = WORK_DIR) -> tuple[bool, bool, str]:
    """Session-end hook: if requirements.txt changed since ``start_sha``, run pip install.

    ``cwd`` is the working tree to diff and install from (the main repo for a
    standard/integration session, or a worker's worktree in parallel mode).

    Returns ``(was_changed, install_succeeded, output)``:
      - ``was_changed`` — True if requirements.txt differed between start_sha
        and the working tree at session end.
      - ``install_succeeded`` — True if the install ran AND exited 0. If
        ``was_changed`` is False, no install runs and this is True (no-op).
      - ``output`` — pip's combined stdout+stderr, or '' if no install ran.

    See issue #278 for the motivation: autodev sessions that ship dependency
    changes commit them to requirements.txt but the dev-machine conda env stays
    out of sync until a human manually re-runs pip, which causes silent import
    crashes the next morning.
    """
    if not _requirements_changed_since(start_sha, cwd):
        return False, True, ""
    success, output = _pip_install_requirements(cwd)
    return True, success, output


def write_board_snapshot(items: list[dict], cwd: Path) -> None:
    """Write a compact board snapshot to ``cwd/board_snapshot.md`` for the session to read.

    Issue #6 (cheaper cold starts): the runner already fetches the board for its
    loop checks, so it hands that state to the session as a Markdown file grouped
    by column. The in-session agent reads this instead of spending its budget
    re-querying the board. ``items`` is the output of :func:`_fetch_all_board_items`;
    if it is empty (fetch failed) the file is still written with a note so the
    session knows to fall back to a live `gh` query. Best-effort: never raises.
    """
    by_status: dict[str, list[dict]] = {}
    for it in items:
        by_status.setdefault(it.get("status", ""), []).append(it)

    lines = [
        "# Board Snapshot",
        "",
        f"_Generated by autodev.py at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}._",
        "",
        ("Use this as the board state for this session instead of re-querying. "
         "If it is missing, empty, or looks stale, fall back to a live `gh` board fetch."),
        "",
    ]
    if not items:
        lines.append("**(no items returned — board fetch was empty or failed; query `gh` directly)**")
    else:
        for status in ("In Review", "In Progress", "Todo", "Backlog", "Blocked", "Done"):
            bucket = by_status.get(status, [])
            if not bucket:
                continue
            lines.append(f"## {status} ({len(bucket)})")
            for it in sorted(bucket, key=lambda x: _issue_sort_key(x.get("number", ""))):
                lines.append(f"- #{it.get('number', '?')}: {it.get('title', '')}")
            lines.append("")

    try:
        (cwd / BOARD_SNAPSHOT_FILE).write_text("\n".join(lines), encoding="utf-8")
    except OSError as e:
        print(f"Warning: could not write board snapshot to {cwd}: {e}")


def _issue_sort_key(number: str) -> int:
    """Sort issues by numeric value, pushing non-numeric IDs to the end."""
    return int(number) if str(number).isdigit() else (1 << 30)


def run_session(
    session_number: int,
    work_dir: Path = WORK_DIR,
    prompt: str = DEFAULT_PROMPT,
    board_items: list[dict] | None = None,
    label: str = "",
) -> int:
    """Run a single autodev session in ``work_dir``. Returns the process exit code.

    ``prompt`` is the instruction handed to Claude — the default drives the full
    priority loop; parallel mode passes the integration/single-issue prompts (see
    :data:`INTEGRATION_PROMPT` / :data:`SINGLE_ISSUE_PROMPT`). ``board_items``, when
    provided, is written to ``work_dir/board_snapshot.md`` before launch so the
    session reads the board cheaply instead of re-querying it (issue #6). ``label``
    tags the log file and console output so parallel workers don't collide.

    After Claude returns, runs the auto-pip-install session-end hook (issue
    #278): if the session modified ``requirements.txt``, runs
    ``pip install -r requirements.txt`` in the conda env so the next session
    and any human work the next morning don't crash on stale imports. The pip
    output is appended to the session log file regardless of success/failure
    so the next reviewer can see what happened.
    """
    DEVLOGS_DIR.mkdir(exist_ok=True)

    if board_items is not None:
        write_board_snapshot(board_items, work_dir)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    tag = f"_{label}" if label else ""
    log_file = DEVLOGS_DIR / f"session_{timestamp}{tag}.txt"

    # Native Claude Code install — the binary Nate actually uses (see _Quickstart_CC.bat).
    # NOT the npm-global claude.cmd, which was a stale 1.0.77 install that the service rejected.
    claude_cmd = r"C:\Users\Nate\.local\bin\claude.exe"
    cmd = [
        claude_cmd,
        "-p",
        "--permission-mode", "bypassPermissions",
        prompt,
    ]

    print(f"\n{'='*60}")
    print(f"Session #{session_number}{(' [' + label + ']') if label else ''} starting at {timestamp}")
    print(f"Working directory: {work_dir}")
    print(f"Log file: {log_file}")
    print(f"Command: {' '.join(cmd)}")
    print(f"{'='*60}\n")

    start_sha = _current_head_sha(work_dir)

    result = subprocess.run(
        cmd,
        cwd=str(work_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=True,
        stdin=subprocess.DEVNULL,
    )

    output_parts = []
    if result.stdout:
        output_parts.append(result.stdout)
    if result.stderr:
        output_parts.append(f"\n--- STDERR ---\n{result.stderr}")
    output_parts.append(f"\n--- EXIT CODE: {result.returncode} ---\n")

    was_changed, install_ok, pip_output = install_requirements_if_changed(start_sha, work_dir)
    if was_changed:
        status = "SUCCEEDED" if install_ok else "FAILED"
        output_parts.append(
            "\n--- POST-SESSION pip install -r requirements.txt ---\n"
            f"{pip_output}\n"
            f"--- pip install {status} ---\n"
        )
        if not install_ok:
            output_parts.append(
                "WARNING: requirements.txt changed this session but pip install failed. "
                "Local conda env is out of sync; rerun manually before the next session.\n"
            )

    full_output = "".join(output_parts)

    log_file.write_text(full_output, encoding="utf-8")
    print(full_output)
    print(f"\nSession #{session_number}{(' [' + label + ']') if label else ''} log saved to: {log_file}")

    return result.returncode


def _git(args: list[str], cwd: Path = WORK_DIR, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a git command in ``cwd`` and return the CompletedProcess (output captured)."""
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=timeout,
    )


def _worktree_add(path: Path) -> bool:
    """Create a detached worktree at ``path`` from ``origin/DEFAULT_BRANCH``. Returns success.

    Detached so the in-session agent is free to create its own ``feature/<n>-...``
    branch inside the worktree (you can't check out the same branch in two
    worktrees). Any stale worktree at ``path`` is removed first. The branch and
    commits the worker makes are pushed to ``origin``, so the local worktree is
    disposable once the session ends.
    """
    if path.exists():
        _worktree_remove(path)
    WORKTREES_DIR.mkdir(parents=True, exist_ok=True)
    _git(["fetch", "origin"], WORK_DIR, timeout=120)
    result = _git(
        ["worktree", "add", "--detach", str(path), f"origin/{DEFAULT_BRANCH}"],
        WORK_DIR,
        timeout=120,
    )
    if result.returncode != 0:
        print(f"Warning: could not create worktree {path}: {result.stderr.strip()}")
        return False
    return True


def _worktree_remove(path: Path) -> None:
    """Remove the worktree at ``path`` (force) and prune stale worktree metadata."""
    _git(["worktree", "remove", "--force", str(path)], WORK_DIR)
    _git(["worktree", "prune"], WORK_DIR)


def _select_build_issues(items: list[dict], n: int) -> list[dict]:
    """Pick up to ``n`` distinct actionable build issues (Todo/In Progress), oldest first.

    The runner assigns each parallel worker a *different* issue from this list, so
    no two workers in the same round touch the same issue. Cross-round races can't
    happen either, because a round waits for all its workers before the next one
    starts. Whether an In Progress item is already mid-flight (recently claimed) is
    left to the in-session claim-check in autodev.md.
    """
    candidates = [it for it in items if it.get("status") in BUILD_STATUSES]
    candidates.sort(key=lambda it: _issue_sort_key(it.get("number", "")))
    return candidates[:n]


def run_cycle(cycle_number: int, workers: int) -> None:
    """Run one autodev cycle.

    With ``workers <= 1`` this is a single standard session (the original
    behavior), now also handed a board snapshot for a cheaper cold start.

    With ``workers > 1`` (issue #4, parallel mode) a cycle is:
      1. One **integration** session, run alone in the main checkout, that does
         Priority 0 (fix/revert a broken default branch) and Priority 1 (review
         and merge ready PRs). Merges touch the default branch, so this must not
         run concurrently with anything.
      2. Up to ``workers`` **build** sessions, each in its own git worktree on a
         distinct Todo/In Progress issue, run in parallel. Each opens a PR and
         moves its item to In Review for a later integration session to merge.
    Worktrees are created from a freshly-fetched ``origin/DEFAULT_BRANCH`` (after
    integration merges land) and torn down when the round finishes.
    """
    items = _fetch_all_board_items()

    if workers <= 1:
        run_session(cycle_number, board_items=items)
        return

    # --- Phase 1: integration (serial, main checkout) ---
    run_session(cycle_number, prompt=INTEGRATION_PROMPT, board_items=items, label="integration")

    # Refresh the default branch + board so workers branch from merged code and
    # pick from current state.
    _git(["fetch", "origin"], WORK_DIR, timeout=120)
    items = _fetch_all_board_items()
    build_issues = _select_build_issues(items, workers)
    if not build_issues:
        print("No actionable build issues for parallel workers this cycle.")
        return

    print(f"\nLaunching {len(build_issues)} parallel build worker(s): "
          f"{', '.join('#' + i['number'] for i in build_issues)}")

    def _run_worker(index: int, issue: dict) -> None:
        wt = WORKTREES_DIR / f"worker-{index}"
        if not _worktree_add(wt):
            return
        try:
            run_session(
                cycle_number,
                work_dir=wt,
                prompt=SINGLE_ISSUE_PROMPT.format(number=issue["number"]),
                board_items=items,
                label=f"issue-{issue['number']}",
            )
        finally:
            _worktree_remove(wt)

    with ThreadPoolExecutor(max_workers=len(build_issues)) as pool:
        futures = [pool.submit(_run_worker, i, issue) for i, issue in enumerate(build_issues)]
        for f in futures:
            f.result()  # surface worker exceptions


def run_until_board_clear(start_count: int, pause_seconds: float, workers: int = 1) -> int:
    """Run cycles until no actionable work remains — board *and* roadmap.

    Runs a cycle, then inspects the board:

    * **Active items remain** → keep going.
    * **Board clear, but it had active items at the *start* of this cycle** (i.e.
      we just merged the last open PR and drained it) → loop once more, so the next
      session can pull the next ``ROADMAP.md`` capability via Priority 5A. Without
      this, the runner quit/stalled the instant the board went "Done-only" after a
      merge instead of advancing the roadmap.
    * **Board clear, and it was *already* clear when this cycle started** → the
      session had its Priority 5A chance and still produced no board work, so there
      is genuinely nothing left to decompose. Return.

    Tying "keep going" to whether sessions actually *produce* board work (rather
    than parsing ROADMAP checkbox state) makes this self-limiting: an exhausted
    roadmap, an all-blocked board, and the 5B fallback idle-stop all converge on
    "a from-clear session left the board clear" → return.

    Shared by ``--until-done`` (which then exits) and ``--always`` (which then
    idle-polls). ``workers`` is passed through to :func:`run_cycle`.
    """
    session_count = start_count
    while True:
        cleared_at_start = not board_has_active_items()
        session_count += 1
        run_cycle(session_count, workers)

        print("\nChecking project board for remaining work...")
        if not board_has_active_items():
            if cleared_at_start:
                # Began clear (had its Priority 5A chance) and ended clear → done.
                return session_count
            # Board only just drained this cycle — give the next session a chance to
            # decompose the next ROADMAP capability before concluding we're done.
            print(f"Board drained this cycle — running one more to advance the roadmap. "
                  f"Pausing {pause_seconds / 60:.1f} min...")
        else:
            print(f"Active items remain. Pausing {pause_seconds / 60:.1f} min before next session...")
        time.sleep(pause_seconds)


def main() -> None:
    """CLI entry point — dispatches single-session, ``--hours``, ``--until-done``, or ``--always`` mode.

    Precedence: ``--always`` > ``--until-done`` > ``--hours``; with none of them,
    runs exactly one session. ``--spacer`` (in minutes) overrides the
    10-second default pause between consecutive sessions, and is ignored
    in single-session mode. When ``--until-done`` exits with no active
    items but one or more Blocked items remain, it invokes
    :func:`send_blocked_alert` before returning. ``--always`` behaves like
    ``--until-done`` but never exits: once the board clears it sends the same
    blocked alert (once per idle period), then polls every ``--poll-interval``
    minutes for newly-eligible work and resumes session runs when it appears.

    ``--workers N`` (default 1) is orthogonal to the loop mode: with N>1 each
    cycle becomes one serial integration session (review/merge) followed by N
    parallel build sessions in git worktrees on distinct issues — see
    :func:`run_cycle`. With N=1 a cycle is a single standard session.
    """
    _force_utf8_stdout()

    parser = argparse.ArgumentParser(description="Autodev runner for Claude Code")
    parser.add_argument(
        "--hours",
        type=float,
        default=0,
        help="Run consecutive sessions for this many hours. 0 = single session (default).",
    )
    parser.add_argument(
        "--until-done",
        action="store_true",
        help="Run sessions until the board is clear AND the ROADMAP has no more "
             "capabilities to decompose (a from-clear session produces no new work), then exit.",
    )
    parser.add_argument(
        "--always",
        action="store_true",
        help="Like --until-done, but never exit: after the board clears, poll every "
             "--poll-interval minutes for new eligible items and resume sessions when found.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=0,
        help="Minutes between board polls while --always is idle (default 60).",
    )
    parser.add_argument(
        "--spacer",
        type=float,
        default=0,
        help="Minutes to wait between sessions (used with --hours, --until-done, or --always).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Parallel build workers per cycle (default 1). N>1 runs one serial "
             "integration session, then N single-issue build sessions in git worktrees.",
    )
    args = parser.parse_args()

    os.chdir(WORK_DIR)

    workers = max(1, args.workers)
    if workers > 1:
        print(f"Parallel mode: up to {workers} build worker(s) per cycle (+1 integration session).")

    # Determine pause between sessions: --spacer (in minutes) or default 10 seconds
    pause_seconds = args.spacer * 60 if args.spacer > 0 else 10

    if args.always:
        # Always mode: run until the board is clear, then poll forever for new work.
        poll_seconds = args.poll_interval * 60 if args.poll_interval > 0 else POLL_INTERVAL_SECONDS

        print("Autodev always mode: run until the board is clear, then poll for new work forever.")
        print(f"Spacer between sessions: {pause_seconds / 60:.0f} min" if args.spacer > 0 else "Spacer between sessions: 10s")
        print(f"Idle poll interval: {poll_seconds / 60:.0f} min")
        print("Stop with Ctrl-C.")
        print(f"{'='*60}")

        session_count = 0
        while True:
            session_count = run_until_board_clear(session_count, pause_seconds, workers)

            # Board is clear. Alert once on any blocked items, then poll until new
            # eligible work appears. The poll only checks the board — it never runs
            # a session — so we don't re-alert on each tick of the same idle period.
            blocked = get_blocked_items()
            if blocked:
                print(f"No active items, but {len(blocked)} item(s) are Blocked.")
                send_blocked_alert(blocked)
            else:
                print("Board is clear — no active items.")

            print(f"Polling every {poll_seconds / 60:.0f} min for new eligible items...")
            while not board_has_active_items():
                time.sleep(poll_seconds)

            print(f"\n{'='*60}")
            print("New eligible items detected — resuming sessions.")
            print(f"{'='*60}")
        # never returns — stop with Ctrl-C

    if args.until_done:
        # Until-done mode: keep running until the board is clear, then exit.
        print("Autodev until-done mode: running until Backlog, Todo, and In Progress are empty.")
        print(f"Spacer between sessions: {pause_seconds / 60:.0f} min" if args.spacer > 0 else "Spacer between sessions: 10s")
        print(f"{'='*60}")

        session_count = run_until_board_clear(0, pause_seconds, workers)

        blocked = get_blocked_items()
        if blocked:
            print(f"No active items, but {len(blocked)} item(s) are Blocked.")
            send_blocked_alert(blocked)
        else:
            print("Board is clear — no items in any active column.")

        print(f"\n{'='*60}")
        print(f"Autodev complete. Ran {session_count} session(s) until board was clear.")
        print(f"{'='*60}")
        return

    if args.hours <= 0:
        # Single session mode
        print("Running single autodev session...")
        run_cycle(1, workers)
        return

    # Timed loop mode
    end_time = datetime.now() + timedelta(hours=args.hours)
    session_count = 0

    print(f"Autodev timed mode: running until {end_time.strftime('%Y-%m-%d %H:%M:%S')} ({args.hours}h)")
    print(f"{'='*60}")

    while datetime.now() < end_time:
        session_count += 1
        remaining = end_time - datetime.now()
        remaining_min = remaining.total_seconds() / 60

        if remaining_min < 1:
            print("\nLess than 1 minute remaining — stopping.")
            break

        print(f"\nTime remaining: {remaining_min:.0f} minutes")

        run_cycle(session_count, workers)

        # Pause between sessions
        if datetime.now() < end_time:
            print(f"\nPausing {pause_seconds / 60:.1f} min before next session...")
            time.sleep(pause_seconds)

    print(f"\n{'='*60}")
    print(f"Autodev complete. Ran {session_count} session(s) over {args.hours}h.")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
