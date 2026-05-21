# autodev.md — Autonomous Development Loop

This document defines an autonomous development workflow for AI coding assistants. When instructed to "follow autodev.md", the AI executes the priority loop below, making one meaningful contribution per session. The goal is to always move the project forward — even when no human is present.

This is designed for unattended execution (e.g., scheduled to run 3-4 times overnight). Every instruction assumes no human is available to answer questions or intervene.

---

## Execution Modes

The `autodev.py` runner tells you which mode you're in via the prompt it hands you. Read the prompt first; if it names a mode, follow that mode's scope. If it just says "follow instructions in autodev.md" with no mode, you are in **Standard mode**.

- **Standard mode** — Run the full priority loop below (Priority 0 → 5), doing exactly one unit of work, then wrap up. This is the default and the only mode when the runner uses a single worker.
- **Integration mode** — Run **Priority 0 and Priority 1 only**: fix or revert a broken default branch, then review and merge every ready PR. Do **not** start new Todo/In Progress build work. The runner uses this as the serial "merge to the default branch" step when running parallel workers, so this is the only session permitted to touch the default branch in that cycle.
- **Single-issue mode** — The prompt names one issue (`work ONLY on issue #N`). Go straight to that issue as Priority 2/3 work. Do **not** review or merge other PRs, do **not** pick up any other issue, and do **not** switch to or merge into the default branch. You are running inside a dedicated **git worktree** the runner created for you (a detached checkout of `origin/<default-branch>`): create your `feature/<N>-...` branch here, do the work, push, open a PR, and move the item to In Review. Skip the "switch back to the default branch" wrap-up step — just leave your feature branch pushed. Multiple single-issue workers run in parallel in separate worktrees, each on a distinct issue, so never assume you are the only session running.

The board-state and merge rules are identical across modes; the modes only narrow *which* priorities you act on so parallel workers don't collide on the default branch.

---

## Prerequisites

Before using this workflow, the project must have:

1. **`github.md`** in the project root (see template at the bottom of this document)
2. **`gh` CLI** installed and authenticated (`gh auth status` must succeed)
3. **`CLAUDE.md`** (or equivalent) describing project architecture, conventions, and constraints
4. **A git remote** configured and pushable (the repo must have a remote named `origin`)

---

## Session Startup (Every Session)

These steps run at the **start of every session**. The AI has no memory of previous sessions — treat every run as a cold start.

1. **Read `github.md`** — load repository name, project board IDs, column option IDs, default branch name, and `gh` CLI path. The **default branch** (e.g., `main` or `master`) specified here is used everywhere this document says "main" — substitute accordingly.
2. **Read `CLAUDE.md`** (or equivalent) — understand project architecture, conventions, and constraints
3. **Set PATH** if `github.md` specifies a non-standard `gh` CLI path
4. **Verify `gh` access** — run `gh auth status`. If it fails, write an error to the session log and stop immediately. Do not retry.
5. **Protect runner-local files** — ensure both `autodev_log.md` and `board_snapshot.md` are listed in `.gitignore` (they are local-only and must never be committed). If either is missing, add it now and commit: `"autodev: gitignore runner-local files"`
6. **Clean git state**:
   - Run `git status` to check for uncommitted changes
   - If there are uncommitted changes: stage all changed files **except** `autodev_log.md` and `board_snapshot.md`, and commit with message `"autodev: save uncommitted work from prior session"`
   - If on a branch other than the default branch: push the current branch (if it has unpushed commits), then switch to the default branch
   - Run `git pull` to get the latest changes
   - **Single-issue mode exception:** you are in a dedicated worktree and own one feature branch. Commit any stray uncommitted work, but do **not** switch to the default branch — stay on (or create) your `feature/<N>-...` branch. Do not `git pull` the default branch into your worktree.
7. **Read the session log** (`autodev_log.md` in project root) if it exists — understand what previous sessions did, especially any noted blockers or bounce-backs
8. **Read `LESSONS.md`** (project root) if it exists — this is the project's accumulated, self-maintained memory of conventions, gotchas, and anti-patterns discovered by prior sessions. Treat it as binding guidance; it exists so you don't repeat past mistakes. You will append to it during wrap-up if you learn something reusable.
9. **Read `ROADMAP.md`** (project root) if it exists — it defines the project's milestones in priority order and is the source of new work when the board runs dry (see Priority 5). Note the current (first not-done) milestone.

---

## Fetching Board State

The project board is the single source of truth.

**First, check for `board_snapshot.md` in the project root.** The runner writes this file immediately before launching you — it lists every board item grouped by column, as of seconds ago. If it exists and looks current (its timestamp is from this session), use it as the board state and **skip the live fetch** — this saves a chunk of your budget on cold-start orientation. Still fetch live details (comments, PR status, CI) for the specific item you act on. If the snapshot is missing, empty, or looks stale (e.g., you're running manually, not via the runner), fall back to fetching the board yourself once at the start:

```bash
"C:\Users\Nate\bin\gh.exe" project item-list 6 --owner nateRaintech --format json --limit 100000
```

**Pagination — read this carefully.** `gh project item-list` (like `gh issue list` and `gh pr list`) defaults to **`--limit 30`**. Omitting `--limit` does NOT fetch everything — it silently caps at 30 and hides the rest. The board will eventually hold hundreds or thousands of items, so you MUST defeat that cap. Two ways:

- **Simple:** pass a `--limit` far larger than the board could ever grow (e.g. `--limit 100000`). `gh` paginates internally up to that bound, so this returns every item in practice.
- **Unbounded (most robust):** use the cursor-paginated GraphQL query (`gh api graphql --paginate`, walking `pageInfo.hasNextPage` until it is false). This is what `autodev.py` uses for its board check and is the only approach that is correct for an arbitrarily large board. See the `_BOARD_GRAPHQL` query in `autodev.py` for the exact shape.

Never rely on the default limit, and never pass a small `--limit` "to be safe" — that is precisely how items past the bound get silently dropped. The JSON structure may vary by `gh` version, so on the first run, inspect the raw output to understand the shape. Typically each item has:
- `id` — the project item ID (used with `gh project item-edit`)
- `content` — contains `number` (issue number), `title`, `type` (Issue or PullRequest), and `repository`
- `status` — the column name as a string (e.g., "In Progress", "Todo", "Done")

**Filter items by column** by matching the `status` field against the column names. For example, to find "In Review" items, filter where `status == "In Review"`.

**Skip "Blocked" items entirely.** Items in the "Blocked" column are waiting on human input or an external dependency. They are not actionable by autodev and must not be counted when determining which priority level has work.

**Skip draft items.** Project boards can contain draft items (notes without a linked issue). These have no issue number and are not actionable by autodev. Ignore them.

**Determine item ordering** by their issue number — lower issue numbers are older and should generally be picked first.

---

## Priority Loop

Work through the following priorities **in order**. Execute the **first category that has actionable items**, complete that unit of work, then proceed to session wrap-up.

```
Priority 0: Broken main  → If CI on the default branch is RED, fix/revert it FIRST (nothing else matters)
Priority 1: In Review    → Validate and test (process ALL items in this column)
Priority 2: In Progress  → Continue work on ONE active task
Priority 3: Todo         → Pick up ONE new task
Priority 4: Backlog      → Refine ONE item and promote it to Todo
Priority 5: Nothing left → Decompose the roadmap into work; only then generate fallback contributions
```

**Important**: Priority 0 and Priority 1 (review) may process MULTIPLE items — a red default branch must be made green, and reviews are lightweight so all In Review items are handled. Priorities 2-5 process exactly ONE item, then stop.

**Fallthrough rule**: If a priority level had items but none were actionable (e.g., all In Review items were escalated due to bounce loops, or all In Progress items were blocked), that priority counts as empty — fall through to the next priority level.

**Blocked column**: Items in the "Blocked" column are invisible to the priority loop. They do not appear in any priority level and are never picked up for work. Only a human can move items out of "Blocked".

---

### Priority 0: Broken Default Branch

**Goal**: A green default branch is the foundation everything else builds on. If it's red, every new branch inherits the breakage and reviews can't be trusted — so fixing it preempts all other work.

**Steps**:

1. Check the status of the latest CI run on the default branch:
   ```bash
   "C:\Users\Nate\bin\gh.exe" run list --branch main --limit 1 --json status,conclusion,workflowName,headSha,url
   ```
   (A small `--limit` is correct here — you want only the most recent run. This is the one place a bounded query is intended.)
2. **If the latest run's `conclusion` is `success`** (or there is no CI configured at all): the default branch is healthy. Skip Priority 0 entirely and proceed to Priority 1.
3. **If the latest run is still in progress** (`status` is `in_progress`/`queued`): do not act on it this session — a fix may already be landing. Proceed to Priority 1.
4. **If the latest run's `conclusion` is `failure`/`timed_out`/`cancelled`**: this is now the session's single unit of work.
   - Read the failing run's logs: `"C:\Users\Nate\bin\gh.exe" run view <RUN_ID> --log-failed`
   - Identify the offending commit/PR (the run's `headSha`).
   - **Prefer a revert** if a single recent merge clearly caused it: `git revert <sha>` on a `fix/revert-<sha>` branch, open a PR, and once *its* CI is green, merge it (Priority 1 rules). Reverting restores green fastest and is low-risk.
   - **Otherwise fix forward**: create a `fix/<short-slug>` branch, fix the failure, push, open a PR with `Closes #<n>` if an issue exists (create one labeled `bug` if not), and let CI verify.
   - Leave a comment on the related issue/PR: `"autodev: default branch CI was red (<summary>). [Reverted <sha> | Fixed in PR #<n>]."`
   - Do this and then proceed to wrap-up. Do not start unrelated work while main is red.

**Parallel mode**: Priority 0 runs inside Integration mode (alongside Priority 1), never inside a single-issue build worker — only the integration session touches the default branch.

---

### Priority 1: In Review

**Goal**: Verify that completed work is correct and move it to Done, or send it back with actionable feedback.

**Steps** (repeat for each item in the "In Review" column):

1. Read the issue description and all comments to understand acceptance criteria
2. Find the linked branch or PR:
   ```bash
   "C:\Users\Nate\bin\gh.exe" pr list --repo nateRaintech/my_notes --state open --json number,title,headRefName,body --search "<issue title or number>"
   ```
3. Check out the branch and read the changes:
   ```bash
   git fetch origin
   git diff main...<BRANCH_NAME>
   ```
4. **Check CI — this is the hard merge gate.** CI is the objective oracle; your own read of the diff is secondary. Get the PR's check status:
   ```bash
   "C:\Users\Nate\bin\gh.exe" pr checks <PR_NUMBER> --repo nateRaintech/my_notes
   ```
   - **If any required check is failing**: treat this as a failed review (go to step 9). Do not merge, regardless of how the diff looks.
   - **If checks are still pending/running**: do not merge this session. Leave the item in "In Review", leave a comment `"autodev: CI still running — deferring merge to a later session."`, and move to the next item.
   - **If checks are all green**: continue to your own review below. Green CI is necessary but not sufficient — still do steps 5–6.
   - **If the repo has no CI configured at all**: fall back to running the test suite yourself as the gate (see step 5), and note "No CI configured — gated on local test run" in your review comment.
5. **If testable**:
   - Run the project's test suite (look in `CLAUDE.md` for the test command, or try `pytest`, `npm test`, `go test ./...`)
   - Only run the test suite if it completes in under 5 minutes. If unsure, run with a timeout.
   - Check for regressions — do ALL tests pass, not just tests related to the change?
   - Review the diff for obvious bugs, security issues, or convention violations
6. **If NOT directly testable** (documentation, config changes, design):
   - Verify the changes match what the issue requested
   - Check for typos, formatting issues, or inconsistencies
7. **Bounce-loop detection**: Read the issue comments. If this item has already been bounced back from "In Review" **2 or more times**, do NOT bounce it again. Instead:
   - Leave a comment: `"autodev: This issue has been reviewed and returned multiple times. Needs human review to resolve. See prior comments for details."`
   - Leave the item in "In Review" — it is now a human's responsibility
   - Move on to the next item
8. **If it passes review (CI green AND your review is clean)**:
   - Switch back to the default branch
   - Merge the PR:
     ```bash
     "C:\Users\Nate\bin\gh.exe" pr merge <PR_NUMBER> --repo nateRaintech/my_notes --squash --delete-branch
     ```
   - Move the project item to "Done"
   - Leave a comment: `"autodev review: Verified — CI green, [brief description of what was checked]. Merged and moved to Done."`
   - **Never merge on a red or missing CI gate.** If you cannot confirm CI is green (and no CI is configured *and* you couldn't run tests locally), leave the item in "In Review" with a comment explaining why, rather than merging blind.
9. **If it fails review** (CI red, or your review found problems):
   - Do NOT merge
   - Switch back to the default branch
   - Move the project item back to "In Progress"
   - Leave a **specific, actionable** comment describing what failed and how to reproduce. If CI failed, paste the failing check name and the relevant log excerpt:
     ```
     autodev review: Failed — [specific failure description; failing CI check + log excerpt if applicable].
     Steps to reproduce: [steps]
     Expected: [what should happen]
     Actual: [what happened]
     ```

---

### Priority 2: In Progress

**Goal**: Continue work on a task that's already been started.

**Steps**:

1. List all items in the "In Progress" column
2. **Claim check**: For each candidate item, read its comments. Skip items where:
   - The most recent comment (within the last 2 hours) says "autodev: Starting work" or similar — another session may be active on it
   - A comment says it's blocked on external input or a human decision
3. Pick the oldest eligible item (lowest issue number)
4. Leave a comment: `"autodev: Continuing work on this."`
5. Find or create the working branch:
   - Check if a branch or PR already exists for this issue
   - If yes: `git fetch origin && git checkout <branch>`
   - If no: create one from the default branch:
     ```bash
     git checkout -b feature/<issue-number>-<short-slug> main
     ```
     Where `<short-slug>` is 2-4 lowercase words from the issue title, joined by hyphens (e.g., `feature/42-add-login-validation`)
6. Read the issue description and all comments to understand what's done and what remains
7. **Do the work**:
   - Write code, fix bugs, update configs — whatever the issue requires
   - Follow project conventions from `CLAUDE.md`
   - Write or update tests if the project has a test framework
   - Run the test suite before committing to ensure nothing is broken
8. **Commit and push**:
   - Stage only the files you changed (no `git add -A`)
   - Write a clear commit message referencing the issue: `"#<number>: <what changed>"`
   - Push the branch: `git push -u origin <branch>`
9. **If the task is complete**:
   - Create a PR if one doesn't exist:
     ```bash
     "C:\Users\Nate\bin\gh.exe" pr create --repo nateRaintech/my_notes --title "<Issue title>" --body "Closes #<ISSUE_NUMBER>\n\n## Changes\n<description>" --base main
     ```
   - Move the project item to "In Review"
   - Leave a comment on the issue: `"autodev: Work complete. PR #<number> created and moved to In Review."`
10. **If the task is NOT completable this session**:
    - Commit and push whatever progress was made
    - Leave a comment: `"autodev: Partial progress. Completed: [X]. Remaining: [Y]."`
    - Keep the item in "In Progress"
11. Switch back to the default branch before ending

---

### Priority 3: Todo

**Goal**: Pick up the next task and start working on it.

**Steps**:

1. List all items in the "Todo" column
2. Pick the oldest item (lowest issue number)
3. Leave a comment: `"autodev: Starting work on this."`
4. Move the item to "In Progress"
5. Create a working branch from the default branch:
   ```bash
   git checkout -b feature/<issue-number>-<short-slug> main
   ```
6. Follow Priority 2, steps 6 onward

---

### Priority 4: Backlog

**Goal**: Refine one backlog item so it's ready for development.

This priority does NOT involve writing code. It's about making an item actionable.

**Steps**:

1. List all items in the "Backlog" column
2. Pick the most well-defined item (or the oldest if similar)
3. Read the issue — assess whether it has enough detail to begin coding
4. **If well-defined** (has clear acceptance criteria, scope is understood):
   - Move to "Todo"
   - Leave a comment: `"autodev: Refined and promoted to Todo. Ready for development."`
5. **If vague or underspecified**:
   - Investigate the codebase to understand the scope and feasibility
   - Leave a comment on the issue with:
     ```
     autodev refinement:
     Proposed approach: [how to implement]
     Files affected: [list of files/modules]
     Estimated scope: [small / medium / large]
     Open questions: [anything that needs human clarification]
     ```
   - If the approach is clear despite the vague description: move to "Todo"
   - If there are genuine open questions that affect implementation: move to **"Blocked"** (not Backlog) with a comment explaining what human input is needed. This prevents future sessions from re-examining the same item.

---

### Priority 5: Nothing Left — Roadmap First, Then Fallback Value

When all active columns (Backlog, Todo, In Progress, In Review) are empty or every item in them has been skipped, there is no queued work. **Before generating anything, look to the roadmap** — that is where real direction comes from. Self-generated filler is the last resort, not the first.

**Blocked-only alert**: If the only remaining items on the board are in the "Blocked" or "Done" columns (i.e., there is nothing actionable anywhere), send an email alert. Use the project's SMTP configuration (from `.env`) to send to the alert address with:
- **Subject**: `autodev: all items blocked — human input needed`
- **Body**: List each blocked item (issue number + title) and a one-line summary of what's needed to unblock it (from the most recent comment).

Send this alert **at most once per session**. It is informational, not a stop signal — continue to roadmap decomposition below.

#### 5A. Roadmap decomposition (DO THIS FIRST)

Read `ROADMAP.md`. It lists milestones in priority order, each with a capability checklist. This is the primary source of new work, and it is **real** work — the autodev-issue cap, idle-stop rule, and prohibited-pattern guards below do **not** apply to it.

1. Find the **current milestone** — the first one not yet marked done.
2. Within it, find the first **unchecked capability** that does not already have an open or recently-closed issue. (Check with `gh issue list --milestone "<milestone title>" --state all`.)
3. Create exactly **one** well-specified issue for that capability:
   - Clear title; body with description, rationale, proposed approach, and explicit **acceptance criteria**.
   - Assign it to the matching GitHub milestone: `--milestone "<milestone title>"`.
   - Begin the body with `"Created by autodev (roadmap)."` so it's identifiable as real roadmap work (distinct from fallback work, which the cap below counts separately).
   - Add it to the board and follow **Priority 3** to start it (move to In Progress, branch, work) — i.e., this counts as your one unit of work this session.
4. **Maintaining the roadmap**: when every capability under the current milestone has a merged/closed issue, check its boxes in `ROADMAP.md`, mark the milestone done, commit (`"autodev: mark <milestone> complete in ROADMAP.md"`), and advance to the next milestone next session. If a capability turns out to need human direction (genuine product decision), leave it unchecked and note the open question in `ROADMAP.md` rather than guessing.

If `ROADMAP.md` is missing, or **every** milestone is marked done, fall through to 5B.

#### 5B. Fallback value generation (only when the roadmap has no remaining work)

This path generates speculative work and is tightly guarded — it exists to avoid idling, not to invent a product direction.

**Fallback-issue cap**: check whether 3 or more open *fallback* issues already exist (this counts only speculative 5B work, not roadmap issues, which are milestone-bound and legitimate):
```bash
"C:\Users\Nate\bin\gh.exe" issue list --repo nateRaintech/my_notes --state open --search "Created by autodev (fallback)" --json number,title
```
If 3 or more exist, **do not create more** — instead work one of them via Priority 3. When creating fallback issues, begin the body with `"Created by autodev (fallback)."`.

**Idle stop rule.** Before creating a new fallback issue, scan `autodev_log.md` for the last 7 session blocks. Count sessions whose **Priority executed** was `5B` (or legacy `5`) AND whose **Issue** title starts with `Test:` or matches a prohibited pattern below. If this count is **≥ 3**, do NOT create new work this session. Instead:

1. Send an email alert (project SMTP) with:
   - **Subject**: `autodev: idle — awaiting human direction`
   - **Body**: list current Backlog items (numbers + titles), list Blocked items, and state "The roadmap is complete and the board has been cycling on fallback work for N consecutive sessions. No new autodev work will be generated until a human extends ROADMAP.md, adds a Priority 1-4 item, or moves a Blocked item to Todo."
2. End the session with a `Priority executed: idle-stopped (5B loop detected)` line in `autodev_log.md`.

Rationale: a finished roadmap plus several straight fallback sessions means the project is waiting on a human to set new direction. Generating more filler obscures that signal. **The fix is to extend `ROADMAP.md`** — that's the lever for steering long-horizon work.

**Pick the highest-value option available**:

**Option A: Write Tests** (preferred if test coverage is incomplete)
- Identify untested or under-tested code paths
- Write unit tests or integration tests
- Create an issue, add it to the board, then work it through the normal flow

**Prohibited test patterns under Option A** (do NOT create issues that match these — they have already caused a test-generation loop on this project):

- Titles starting with `Test: pin`, `Test: AST`, `Test: cross-file`, or containing `pin literal`
- Bodies whose goal is "verify doc X matches constant Y in code" or "AST-parse verify Z is present"
- Tests that re-assert invariants already covered by an existing test file
- Set-identity checks between a Markdown list and a Python list/set
- Any test whose failure would be triggered only by a deliberate human edit to docs or string literals (not a behavior regression)

Rationale: these tests lock editorial content as test fixtures. They never catch real bugs — they only flag intentional doc/constant edits, forcing coordinated test updates and creating a maintenance tax. If cross-file drift ever causes a real bug, fix it with a single CI script, not with per-pair test files.

**If the only Option A test idea you can think of fits a prohibited pattern, skip Option A entirely** and move to Option B, C, or D.

**Option B: Code Quality**
- Run linters, type checkers, or static analysis if configured
- Fix warnings, remove dead code, improve error handling
- Create an issue and work it

**Option C: Improve Documentation**
- Find undocumented public functions, modules, or setup steps
- Update existing docs (do NOT create new markdown files unless the project has none)
- Create an issue and work it

**Option D: Propose Improvements** (only if A-C have nothing to do)
- Create a well-specified issue in "Backlog" with:
  - Clear title and description
  - Rationale (why it matters)
  - Proposed approach
  - Acceptance criteria
- Do NOT work on it this session — just create the issue and stop

**For Options A-C**: Create the issue first, add it to the project board, then work it through the full flow (Todo → In Progress → In Review).

---

## Rules

1. **One unit of work per session.** Do one thing well. Don't try to clear the board.
2. **Always leave a trail.** Prefix all issue comments with `autodev:` so humans can easily identify automated activity. Future sessions depend on this context.
3. **Never force-push or rewrite history.** No `--force`, no `--amend` on pushed commits, no `reset --hard`.
4. **Merge only on a green CI gate.** Never merge a PR whose CI checks are failing, missing, or still pending. When CI is green *and* your review is clean, merge with `--squash --delete-branch`. If a project has no CI, gate on a local test run instead. CI is the objective oracle — trust it over your own read of the diff. (See Priority 1, step 4.)
5. **Respect project conventions.** Follow `CLAUDE.md` patterns. Match existing code style. Don't introduce new patterns.
6. **Skip when stuck, don't block.** If a task is ambiguous and you cannot resolve it by reading the codebase and issue comments, leave a comment with your questions, then **move on to the next priority**. Never block an entire session on one unclear task.
7. **Keep commits atomic.** One logical change per commit. Reference the issue number in the message.
8. **Limit self-generated work.** Only reach Priority 5 when nothing else is actionable. Roadmap decomposition (5A) is real, milestone-bound work and is preferred. Speculative fallback work (5B) is capped: maximum 1 new fallback item per session, maximum 3 open fallback issues (`"Created by autodev (fallback)"`) at any time.
9. **Test before advancing.** If the project has tests, run them before pushing. If your changes break something, fix it before moving on.
10. **Always return to the default branch.** Before ending the session, ensure the working directory is on the default branch with a clean `git status`. **Exception — single-issue mode:** you own one worktree and feature branch; leave it pushed and do not switch to the default branch.
11. **Log everything.** Write a session summary to `autodev_log.md` before exiting (see Session Wrap-Up).
12. **Never commit secrets.** Do not stage or commit files matching `.env`, `*.pem`, `*.key`, `credentials.*`, or anything listed in `.gitignore`. If you encounter such files in `git status`, leave them unstaged.
13. **Avoid costly operations unless required.** Do not run commands that incur external API costs (e.g., calling paid LLM APIs, provisioning cloud resources) unless the issue you're working on specifically requires it. Test suites that make real API calls should be skipped in favor of unit tests or mocked tests when available.
14. **Roadmap drives new work.** When the board is dry, decompose `ROADMAP.md` before generating any speculative filler (Priority 5A before 5B). Keep `ROADMAP.md` current: check off capabilities and mark milestones done as their issues merge.
15. **Record lessons.** When you discover a reusable convention, a non-obvious gotcha, or an anti-pattern that wasted a session (the kind of thing the "prohibited test patterns" list captures), append it to `LESSONS.md` so future cold-start sessions inherit it. The framework should get smarter over time, not repeat mistakes.

---

## Session Wrap-Up

At the end of every session, perform these steps:

1. **Ensure clean git state**: checkout the default branch, verify `git status` is clean. (Single-issue mode: stay on your pushed feature branch — see Rule 10.)
2. **Update `LESSONS.md` if you learned something reusable.** If this session surfaced a convention, gotcha, flaky behavior, or anti-pattern worth carrying forward, append a dated one-liner to `LESSONS.md` and commit it (`"autodev: record lesson — <summary>"`). This is the project's compounding memory; unlike `autodev_log.md` it **is** committed and shared. Skip if nothing reusable came up.
3. **Update `ROADMAP.md` if a milestone advanced.** If your work completed the last open capability under the current milestone, check its boxes / mark it done and commit (see Priority 5A).
4. **Append to session log**: Write the following to `autodev_log.md` in the project root (create the file if it doesn't exist, append if it does):

```markdown
---
### Session: <YYYY-MM-DD HH:MM>

**Mode**: <Standard / Integration / Single-issue #N>
**Priority executed**: <0-5B and description>
**Issue**: #<number> — <title>
**Board movement**: <from column> → <to column>
**Changes**: <brief description of what was done>
**Branch/PR**: <branch name or PR link, or "N/A">
**CI**: <Green / Red / Pending / No CI configured>
**Tests**: <Passed / Failed / No test suite / Not applicable>
**Lesson recorded**: <one-liner appended to LESSONS.md, or "None">
**Blockers**: <any issues needing human attention, or "None">
**Next suggested action**: <what the next session should focus on>
---
```

5. **Do NOT push `autodev_log.md` or `board_snapshot.md` to the remote.** They're local-only files. (Session Startup step 5 ensures they're in `.gitignore`.) `LESSONS.md` and `ROADMAP.md`, by contrast, ARE committed.

---

## Runner-Side: Auto-Reinstall After `requirements.txt` Changes

The `autodev.py` runner (not the in-session AI) handles dependency drift. After each Claude session returns, `run_session()`:

1. Compares the working tree against the HEAD SHA captured at session start.
2. If `requirements.txt` changed, runs `python -m pip install -r requirements.txt` in the conda env.
3. Appends the pip output to the per-session `devlogs/session_<timestamp>.txt` log and prints it to stdout.
4. If pip exits non-zero, prints a warning that the local env is out of sync.

This exists because CI builds its own venv, so a PR that adds packages to `requirements.txt` merges cleanly even though the dev-machine env still has the old versions — the next interactive session crashes on import the following morning. With the post-session hook, the dev env stays current automatically after every shipping session.

The in-session AI does NOT need to do anything for this — it's invisible from inside the session. Just edit `requirements.txt` normally when adding dependencies, and the runner picks it up after the session ends.

---

## gh Command Reference

All IDs below come from `CLAUDE.md` under "GitHub Project Board IDs".

```bash
# Verify authentication
"C:\Users\Nate\bin\gh.exe" auth status

# Fetch all project board items (filter by status field in the JSON response).
# --limit defaults to 30 — pass a huge limit so gh paginates through everything.
"C:\Users\Nate\bin\gh.exe" project item-list 6 --owner nateRaintech --format json --limit 100000

# Move an item to a different column
"C:\Users\Nate\bin\gh.exe" project item-edit --project-id PVT_kwHODNNZlM4BYaSW --id <ITEM_ID> \
  --field-id PVTSSF_lAHODNNZlM4BYaSWzhTgWZw --single-select-option-id <COLUMN_OPTION_ID>

# Read an issue (description + comments)
"C:\Users\Nate\bin\gh.exe" issue view <ISSUE_NUMBER> --repo nateRaintech/my_notes
"C:\Users\Nate\bin\gh.exe" issue view <ISSUE_NUMBER> --repo nateRaintech/my_notes --comments

# Comment on an issue (always prefix with "autodev:")
"C:\Users\Nate\bin\gh.exe" issue comment <ISSUE_NUMBER> --repo nateRaintech/my_notes --body "autodev: <message>"

# Create a new issue (optionally assign it to a roadmap milestone)
"C:\Users\Nate\bin\gh.exe" issue create --repo nateRaintech/my_notes --title "<title>" --body "<body>" --milestone "<milestone title>"

# List issues for a roadmap milestone (to check what's already created / done)
"C:\Users\Nate\bin\gh.exe" issue list --repo nateRaintech/my_notes --milestone "<milestone title>" --state all --limit 100000

# Add an issue to the project board
"C:\Users\Nate\bin\gh.exe" project item-add 6 --owner nateRaintech --url <ISSUE_URL>

# Find PRs for a branch
"C:\Users\Nate\bin\gh.exe" pr list --repo nateRaintech/my_notes --head <BRANCH_NAME> --json number,title,state

# Create a PR (always link to the issue with "Closes #N")
"C:\Users\Nate\bin\gh.exe" pr create --repo nateRaintech/my_notes --title "<title>" \
  --body "Closes #<ISSUE_NUMBER>" --base main

# Merge a PR (squash and clean up)
"C:\Users\Nate\bin\gh.exe" pr merge <PR_NUMBER> --repo nateRaintech/my_notes --squash --delete-branch

# Check PR status and CI results (the hard merge gate — see Priority 1, step 4)
"C:\Users\Nate\bin\gh.exe" pr checks <PR_NUMBER> --repo nateRaintech/my_notes

# Check CI on the default branch (Priority 0 — latest run only, so a small --limit is correct)
"C:\Users\Nate\bin\gh.exe" run list --branch main --limit 1 --json status,conclusion,workflowName,headSha,url

# Read the failing logs of a CI run (Priority 0)
"C:\Users\Nate\bin\gh.exe" run view <RUN_ID> --repo nateRaintech/my_notes --log-failed
```

---

## Handling Edge Cases

**Stale "In Progress" items**: If an item has been "In Progress" with no comments or commits in the last 7 days (check comment timestamps), treat it as abandoned. Leave a comment asking for status, but still work on it if you can.

**Bounce loops**: If an item has been moved from "In Review" back to "In Progress" 2+ times (count "autodev review: Failed" comments), escalate to human attention instead of bouncing again. See Priority 1, step 7 (bounce-loop detection).

**Blocked items**: If an issue comment contains "blocked", "waiting on", or "needs human", move the item to the **"Blocked"** column (not just skip it) and leave a comment summarizing what's needed. This ensures the board reflects reality and prevents future sessions from re-examining the same stuck item. Only a human should move items out of "Blocked".

**Merge conflicts**: If `git merge` or `git rebase` against the default branch shows conflicts:
- If fewer than 3 files are conflicted and the resolution is obvious: resolve, commit, and continue
- Otherwise: leave a comment `"autodev: Merge conflicts with the default branch in [files]. Needs manual resolution."` and move on to the next item

**Failing CI**: If `gh pr checks` shows failures:
- Read the failure output
- If it's related to the PR's changes: attempt a fix
- If it's a pre-existing or flaky failure: note it in a comment and proceed with the review

**Empty board with maximum autodev issues**: If Priority 5B (fallback) is reached but 3+ open fallback issues already exist, do not create more. Instead, pick one of the existing fallback issues and work on it via Priority 3. (This cap does not apply to roadmap issues in 5A.)

**No test suite**: If the project has no tests and no test framework configured, skip the "run tests" substeps. Note in the session log: "No test suite configured."

**Large tasks**: If a Todo item looks like it requires more than ~200 lines of changes or touches more than 5 files, break it into sub-issues before starting. Create the sub-issues, link them to the parent with a comment, and work on the smallest sub-issue first.

**Permission or auth errors**: If any `gh` command fails with a 401/403 or authentication error, log the error to `autodev_log.md` and stop the session immediately. Do not retry.
