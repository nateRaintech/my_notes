# GitHub Workflow Standards

This document defines Git and GitHub standards. **Claude Code must follow these instructions for all git operations.**

For one-time setup (CLI install, project board creation, GraphQL reference), see `github_setup.md`.

---

## Branch Strategy

- **Never commit directly to `main`.** All work happens on feature/fix branches.
- Branch naming convention:
  - `feature/<short-description>` — new functionality
  - `fix/<short-description>` — bug fixes
  - `refactor/<short-description>` — code cleanup, no behavior change
  - `chore/<short-description>` — tooling, config, dependencies
- When starting new work, always create a branch from the latest `main`.
- If a GitHub Issue exists for the work, include the issue number: `feature/42-user-auth-flow`.

## Commits

- Make **atomic commits** as logical units of work are completed — not one giant commit at the end.
- Write commit messages in imperative mood, descriptive of what the commit does:
  - Good: `add input validation to upload form`
  - Good: `fix CSV export timeout on large datasets`
  - Bad: `updates`, `wip`, `fixed stuff`
- If a commit relates to a GitHub Issue, reference it: `add retry logic for API calls (refs #14)`.

## Pull Requests

- When a feature/fix branch is ready (or at the end of a work session if work is ongoing), **open a pull request** to `main`.
- PR title should clearly describe the change.
- PR description must include:
  - **What** changed and **why**.
  - Any notable implementation decisions or trade-offs.
  - If it closes an issue, include `Closes #<number>` to auto-link it.
- If the work is still in progress, open a **draft PR** and note what remains.

## Issues

- When new bugs, features, or tech debt are identified during development, **create a GitHub Issue** rather than leaving a TODO comment.
- Issues should have:
  - A clear, specific title.
  - A short description with enough context to understand the problem/request.
  - Appropriate labels: `bug`, `enhancement`, `tech-debt`, `documentation`.
- When closing an issue via PR, always use the `Closes #<number>` keyword in the PR description.

## General Rules

- Always `git pull` the latest `main` before creating a new branch.
- If asked to "commit" or "push" without further context, follow all the above standards — don't just dump everything into one commit on whatever branch is checked out.
- If the current branch is `main` and there are uncommitted changes, create an appropriately named branch first, then commit.
- Keep the repo clean: don't commit environment files, secrets, build artifacts, or anything that belongs in `.gitignore`.

---

## Project Board (Kanban)

We use a GitHub Project (Kanban board) to track work visually.

### Board Columns

| Column | What goes here |
|--------|----------------|
| **Backlog** | Ideas and future work not yet planned |
| **Todo** | Planned for current/next sprint |
| **In Progress** | Actively being worked on |
| **In Review** | PR open, awaiting review/merge |
| **Blocked** | Waiting on human input, external dependency, or unresolved questions |
| **Done** | Merged to main, completed |

### Kanban Workflow

1. **New work identified** → Create a GitHub Issue → Add to board (Backlog or Todo)
2. **Starting work** → Move card to "In Progress" → Create branch referencing issue number
3. **PR opened** → Move card to "In Review"
4. **PR merged** → Move card to "Done"
5. **Item blocked** → Move card to "Blocked" with a comment explaining what's needed → Resume from prior column once unblocked

### Tips

- Keep cards updated — the board is how stakeholders see progress
- Add comments to cards for context (decisions, blockers, questions)
- Use labels on issues to categorize: `bug`, `enhancement`, `tech-debt`
- Move items to **Blocked** rather than leaving them in an active column when they can't progress — this keeps the board honest and prevents automated workflows from spinning on unactionable items

---

## Board Sync: Claude Code Integration

**Claude Code must keep the project board in sync with all work it performs.** The board is how stakeholders track progress — it must reflect reality at all times.

Project-specific IDs (PROJECT_ID, STATUS_FIELD_ID, column option IDs) are stored in `CLAUDE.md` under the **GitHub Project Board IDs** section. Read those IDs before performing any board operations.

### When the user asks you to do something (feature, fix, refactor, etc.):

1. **Check for existing issue** — Run `gh issue list` and check if an issue already exists for this work.
2. **Create issue if needed** — If no issue exists, create one with an appropriate label.
3. **Add to project board** — Add the issue to the project and move it to **In Progress**.
4. **Create branch** — Branch from latest `main`, referencing the issue number (e.g., `feature/16-add-dark-mode`).
5. **Do the work** — Commit in small, logical increments referencing the issue (e.g., `refs #16`).
6. **Open PR** — When work is ready (or at end of session), open a PR with `Closes #<number>` in the body. Move card to **In Review**.
7. **If merged** — Move card to **Done**.
8. **If work is incomplete** — Open a **draft PR**, leave card in **In Progress**, and note what remains.

### When bugs or tech debt are discovered during work:

1. Create a new GitHub Issue with the appropriate label (`bug`, `tech-debt`).
2. Add it to the project board in **Backlog** or **Todo**.
3. Do NOT silently fix it without tracking — the board should reflect all work.

### Session start:

1. Run `gh issue list` and `gh pr list` to check current state.
2. Briefly summarize open issues, in-progress PRs, and board status before starting new work.

### Session end:

1. Ensure all work is committed and pushed.
2. Open or update a PR with a current description.
3. Verify the board reflects the current state of all work items.

### Moving cards (quick reference):

```bash
# Add issue to project
gh project item-add 6 --owner nateRaintech --url <ISSUE_URL>

# Get item ID for the issue (needed for moves)
gh project item-list 6 --owner nateRaintech

# Move item to a column
gh project item-edit \
  --project-id PVT_kwHODNNZlM4BYaSW \
  --id <ITEM_ID> \
  --field-id PVTSSF_lAHODNNZlM4BYaSWzhTgWZw \
  --single-select-option-id <COLUMN_OPTION_ID>
```

Column option IDs are stored in `CLAUDE.md`. For full GraphQL reference, see `github_setup.md`.

---

## CLI Environment Notes (Windows)

This project runs on Windows. `gh` lives at `C:\Users\Nate\bin\gh.exe`. The
autodev runner (`autodev.py`) and `autodev.md` call it by that full path, since
`gh` is not necessarily on the interactive shell's `PATH`. From the Bash tool
(git-bash), `gh` resolves on `PATH`; from PowerShell, prefer the full path.

```bash
# Bash tool (git-bash): gh is on PATH
gh issue list --repo nateRaintech/my_notes

gh pr create --title "fix: description here" --body "$(cat <<'EOF'
## Summary
- Change description

## Test plan
- [x] Verified manually
EOF
)"
```

```powershell
# PowerShell: call gh by full path
& "C:\Users\Nate\bin\gh.exe" issue list --repo nateRaintech/my_notes
```

### Pagination — IMPORTANT

`gh issue list`, `gh pr list`, and `gh project item-list` all default to **`--limit 30`**. Omitting `--limit` does **not** fetch everything — it silently returns only the first 30 and hides the rest. The board and issue list will eventually hold hundreds or thousands of items, so you must always defeat that cap when you need the full set:

- **Simple:** pass a `--limit` far larger than the list could ever grow, e.g. `gh issue list --limit 100000`. `gh` paginates internally up to that bound, so you get everything in practice.
- **Unbounded (most robust):** use `gh api graphql --paginate`, walking `pageInfo.hasNextPage` until it is false. This is correct for any size and is what `autodev.py` uses for its board check.

When you genuinely only need a subset, narrow with `--state` or `--search` filters — not with a small `--limit`, which silently drops matching items past the bound.

### GraphQL string escaping

When using `gh api graphql`, prefer single-quoted `-f query='...'` so `$`, backticks, and quotes inside the query don't get re-interpreted by the shell.
