# GitHub Project Setup Reference

One-time setup commands for GitHub CLI and project boards. For daily workflow standards, see `github.md`.

> **This project is already set up.** The board is **user-owned** by `nateRaintech`,
> project **#6** (https://github.com/users/nateRaintech/projects/6). Because the owner
> is a **User, not an organization**, use the `user(login: ...)` GraphQL variants in
> this doc — not `organization(...)`. All concrete IDs (PROJECT_ID, STATUS_FIELD_ID,
> column option IDs) live in `CLAUDE.md` → "GitHub Project Board IDs". This file is
> retained as a reference for re-running or extending setup.

---

**IMPORTANT!!! — Pagination**
`gh issue list`, `gh pr list`, and `gh project item-list` default to **`--limit 30`**. Omitting `--limit` does NOT paginate through everything — it silently returns only the first 30 and hides the rest. The project may eventually have hundreds or thousands of items, so when you need the full set, pass a `--limit` far larger than the list could ever grow (e.g. `--limit 100000`, which makes `gh` paginate internally), or use `gh api graphql --paginate` for unbounded cursor pagination. Narrow with `--state`/`--search` filters when you only need a subset — never with a small `--limit`.

## GitHub CLI Setup

**Prerequisites:** Ensure `gh` CLI is installed and authenticated before running project commands.

### Check if gh CLI is installed

```bash
gh --version
```

### Install gh CLI (if needed)

```bash
# Linux (Debian/Ubuntu)
sudo apt install gh

# Linux (Fedora)
sudo dnf install gh

# macOS
brew install gh

# Windows (winget)
winget install --id GitHub.cli
```

### Authenticate gh CLI

```bash
gh auth login -h github.com -p https -w
```

This opens a browser for OAuth authentication. After auth, add project scopes (both `read:project` and `project` are required):

```bash
gh auth refresh -h github.com -s read:project -s project
```

**Note:** Both `gh auth login` and `gh auth refresh` may open a browser for OAuth. In non-interactive environments, the CLI prints a one-time code and URL to complete auth manually.

---

## Project Setup Commands

When asked to set up a GitHub Project for a repository, use these commands.

### 1. Determine the Owner

The owner is either a GitHub **user** or an **organization**. This matters for GraphQL queries later.

```bash
# Get the authenticated user's login
gh api user --jq '.login'

# Get the repo owner (may be a user or org)
gh repo view --json owner --jq '.owner.login'

# Check if the repo owner is an org or user (look for "Organization" or "User" type)
gh api repos/<OWNER>/<REPO> --jq '.owner.type'
```

### 2. Create a Project

```bash
gh project create --owner <OWNER> --title "<Project Name>"
```

**Note:** This command does not output the project number. Use `gh project list` (next step) to find it.

New projects are **private** by default and **not linked** to any repository. You must link and optionally change visibility after creation (see steps below).

### 3. List Projects (to get project number)

```bash
gh project list --owner <OWNER>
```

Output shows: `NUMBER  TITLE  STATE  ID`

### 4. Get Project Field IDs

To manipulate the board, you need field IDs. Get the Status field and its options:

```bash
# For user-owned repos:
gh api graphql -f query='
query {
  user(login: "<OWNER>") {
    projectV2(number: <PROJECT_NUMBER>) {
      id
      field(name: "Status") {
        ... on ProjectV2SingleSelectField {
          id
          options { id name }
        }
      }
    }
  }
}'

# For organization-owned repos:
gh api graphql -f query='
query {
  organization(login: "<OWNER>") {
    projectV2(number: <PROJECT_NUMBER>) {
      id
      field(name: "Status") {
        ... on ProjectV2SingleSelectField {
          id
          options { id name }
        }
      }
    }
  }
}'
```

**Save these IDs:**
- `projectV2.id` → PROJECT_ID (e.g., `PVT_kwHO...`)
- `field.id` → STATUS_FIELD_ID (e.g., `PVTSSF_lAHO...`)
- `options[].id` → Column option IDs (e.g., `dc008eeb` for Backlog)

### 5. Customize Board Columns

New projects only have **Todo**, **In Progress**, and **Done** by default. To add the standard columns (Backlog, In Review, Blocked), update the Status field options:

```bash
gh api graphql -f query='
mutation {
  updateProjectV2Field(input: {
    fieldId: "<STATUS_FIELD_ID>"
    singleSelectOptions: [
      {name: "Backlog", color: GRAY, description: "Future work and ideas"}
      {name: "Todo", color: BLUE, description: "Committed to doing soon"}
      {name: "In Progress", color: YELLOW, description: "Currently being worked on"}
      {name: "In Review", color: ORANGE, description: "PR open, awaiting merge"}
      {name: "Blocked", color: RED, description: "Waiting on human input or external dependency"}
      {name: "Done", color: GREEN, description: "Completed"}
    ]
  }) {
    projectV2Field {
      ... on ProjectV2SingleSelectField {
        id
        options { id name }
      }
    }
  }
}'
```

**Important:** This replaces ALL column options. Always include all desired columns in the mutation. Save the new option IDs from the response — they will change.

Available colors: `GRAY`, `BLUE`, `GREEN`, `YELLOW`, `ORANGE`, `RED`, `PINK`, `PURPLE`.

### 6. Link Project to Repository

New projects are not linked to any repository by default. Without linking, the project won't appear on the repo's **Projects** tab.

```bash
# Get the repository node ID
gh api graphql -f query='
query {
  repository(owner: "<OWNER>", name: "<REPO>") { id }
}'

# Link the project to the repository
gh api graphql -f query='
mutation {
  linkProjectV2ToRepository(input: {
    projectId: "<PROJECT_ID>"
    repositoryId: "<REPOSITORY_NODE_ID>"
  }) {
    repository { name }
  }
}'
```

**Optional: Change visibility** (requires org admin for org-owned projects):

```bash
gh api graphql -f query='
mutation {
  updateProjectV2(input: {
    projectId: "<PROJECT_ID>"
    public: true
  }) {
    projectV2 { public url }
  }
}'
```

If you get a "not authorized to change project visibility" error, change it manually: Project Settings (gear icon) > Visibility > Public.

### 7. Create Issues

```bash
gh issue create \
  --title "Issue title" \
  --body "Issue description" \
  --label "enhancement"
```

### 8. Add Issues to Project

```bash
gh project item-add <PROJECT_NUMBER> --owner <OWNER> --url <ISSUE_URL>
```

### 9. List Project Items

```bash
gh project item-list <PROJECT_NUMBER> --owner <OWNER>
```

### 10. Get Item IDs with Status

```bash
# For user-owned repos:
gh api graphql -f query='
query {
  user(login: "<OWNER>") {
    projectV2(number: <PROJECT_NUMBER>) {
      items(first: 50) {
        nodes {
          id
          content {
            ... on Issue { number title }
          }
          fieldValueByName(name: "Status") {
            ... on ProjectV2ItemFieldSingleSelectValue { name optionId }
          }
        }
      }
    }
  }
}'

# For organization-owned repos:
gh api graphql -f query='
query {
  organization(login: "<OWNER>") {
    projectV2(number: <PROJECT_NUMBER>) {
      items(first: 50) {
        nodes {
          id
          content {
            ... on Issue { number title }
          }
          fieldValueByName(name: "Status") {
            ... on ProjectV2ItemFieldSingleSelectValue { name optionId }
          }
        }
      }
    }
  }
}'
```

### 11. Move Item Between Columns

```bash
gh project item-edit \
  --project-id <PROJECT_ID> \
  --id <ITEM_ID> \
  --field-id <STATUS_FIELD_ID> \
  --single-select-option-id <COLUMN_OPTION_ID>
```

### 12. Close an Issue

```bash
gh issue close <ISSUE_NUMBER> -c "Completed"
```

### 13. Create Milestones (mirror ROADMAP.md)

Autodev's Priority 5A decomposes `ROADMAP.md` into issues filed against GitHub Milestones whose **titles match the roadmap milestone headings exactly** (e.g. `M2: Core Capability`). Create one milestone per roadmap entry:

```bash
# Create a milestone (REST API — gh has no native `milestone create` command)
gh api repos/<OWNER>/<REPO>/milestones -f title="M1: Project Foundation" -f description="Builds, runs, green CI"

# List milestones (to get titles/numbers)
gh api repos/<OWNER>/<REPO>/milestones --jq '.[] | "\(.number)\t\(.title)"'
```

Keep titles in sync with `ROADMAP.md`. When a milestone is fully delivered, autodev marks it done in `ROADMAP.md`; you can also close the GitHub milestone with `gh api --method PATCH repos/<OWNER>/<REPO>/milestones/<NUMBER> -f state=closed`.

---

## CI Setup — the Autodev Merge Gate

Autodev will **not merge a PR unless CI is green**, and it treats a red default branch as Priority 0 (drop everything and fix). So the project must have CI that runs on PRs. Create `.github/workflows/ci.yml`, adapting the test/lint steps to the project's stack (read `CLAUDE.md` for the actual commands). Generic skeleton:

```yaml
name: CI
on:
  pull_request:
    branches: [ main ]   # use the repo's default branch
  push:
    branches: [ main ]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      # --- adapt the steps below to the project's stack ---
      - uses: actions/setup-python@v5      # e.g. Python; swap for setup-node, etc.
        with:
          python-version: "3.x"
      - name: Install deps
        run: pip install -r requirements.txt
      - name: Lint
        run: ruff check .                  # or flake8 / eslint / golangci-lint ...
      - name: Test
        run: pytest -q                     # or npm test / go test ./... ...
```

Notes:
- The job names become the check names autodev reads via `gh pr checks` and `gh run list --branch <default>`. A failing required check blocks merges automatically.
- If the project has **no tests yet**, still commit the scaffold (e.g. a job that just runs the linter or a `pytest --co` collection check) so the gate exists and tightens as tests are added. Add "wire up real CI tests" as the first `ROADMAP.md` capability.
- Don't make CI call paid external APIs — keep it to unit/mocked tests so unattended runs stay cheap.

---

## Full Project Setup Checklist

When setting up a new repository with full GitHub integration:

### Initial Setup

- [ ] Ensure `gh` CLI is installed
- [ ] Run `gh auth login` for git operations
- [ ] Run `gh auth refresh -h github.com -s read:project -s project` for project access

### Create Project Board

- [ ] Determine owner type (`user` or `organization`) — affects GraphQL queries
- [ ] Create project: `gh project create --owner <OWNER> --title "<Name>"`
- [ ] Note the project number from `gh project list`
- [ ] Get field IDs using GraphQL query (step 4)
- [ ] Save PROJECT_ID, STATUS_FIELD_ID, and column option IDs
- [ ] Customize columns using `updateProjectV2Field` mutation (step 5)
- [ ] Save the new column option IDs from the mutation response
- [ ] Link project to repository (step 6) — required for it to appear on the repo's Projects tab
- [ ] Optionally set project visibility to public (step 6)

### Standard Columns

New projects default to **Todo / In Progress / Done**. Use the `updateProjectV2Field` mutation to set up all six standard columns:

| Column | Purpose | Color |
|--------|---------|-------|
| Backlog | Future work, ideas | GRAY |
| Todo | Committed to doing soon | BLUE |
| In Progress | Currently being worked on | YELLOW |
| In Review | PR open, awaiting merge | ORANGE |
| Blocked | Waiting on human input or external dependency | RED |
| Done | Completed | GREEN |

### Create Labels

GitHub provides default labels (`bug`, `enhancement`, `documentation`, etc.) but you may need to create additional ones:

```bash
# Create tech-debt label (referenced in workflow standards)
gh label create "tech-debt" --description "Technical debt and code cleanup" --color "fbca04"

# List existing labels
gh label list
```

### Autodev Framework Wiring

- [ ] Move `ROADMAP.md` to project root; populate from CLAUDE.md goals if clear, else leave the skeleton
- [ ] Move `LESSONS.md` to project root (commit seed content as-is)
- [ ] Create a **GitHub Milestone** per `ROADMAP.md` milestone, titles matching exactly (step 13)
- [ ] Create `.github/workflows/ci.yml` adapted to the stack — the autodev merge gate (see "CI Setup")
- [ ] Add `autodev_log.md`, `board_snapshot.md`, and `devlogs/` to `.gitignore`
- [ ] Set `DEFAULT_BRANCH` (and `WORK_DIR`/`PY_EXE`/`GH_CLI`/`PROJECT_*`) in `autodev.py`
- [ ] Verify: `python autodev.py --help` lists `--always`, `--workers`, `--poll-interval`

### Populate Board

- [ ] Create issues for planned work: `gh issue create`
- [ ] Add issues to project: `gh project item-add`
- [ ] Move items to appropriate columns: `gh project item-edit`
- [ ] Close completed issues: `gh issue close`

### For Completed Work (showing history)

To show work that was already completed before the board existed:

```bash
# Create issue
gh issue create --title "Feature X" --body "Description" --label "enhancement"

# Close it immediately with comment
gh issue close <NUMBER> -c "Completed <DATE>"

# Add to project (will show as closed)
gh project item-add <PROJECT_NUMBER> --owner <OWNER> --url <ISSUE_URL>

# Move to Done column
gh project item-edit --project-id <ID> --id <ITEM_ID> --field-id <STATUS_ID> --single-select-option-id <DONE_OPTION_ID>
```

### After Setup: Save IDs to CLAUDE.md

After completing setup, add a **GitHub Project Board IDs** section to `CLAUDE.md` with the project-specific IDs. This allows Claude Code to sync with the board during regular work sessions without re-querying.

---

## Quick Reference: Common Operations

| Task | Command |
|------|---------|
| List issues | `gh issue list` |
| Create issue | `gh issue create --title "X" --body "Y" --label "Z"` |
| Close issue | `gh issue close <N> -c "Done"` |
| List PRs | `gh pr list` |
| Create PR | `gh pr create --title "X" --body "Y"` |
| List projects | `gh project list --owner <OWNER>` |
| Add to project | `gh project item-add <N> --owner <O> --url <URL>` |
| List project items | `gh project item-list <N> --owner <OWNER>` |
| Move item | `gh project item-edit --project-id X --id Y --field-id Z --single-select-option-id W` |

---

## Notes for Claude Code

1. **Always check auth first** — Run `gh auth status` before GitHub operations.
2. **Project scopes required** — Run `gh auth refresh -h github.com -s read:project -s project` if you get scope errors. Both scopes are needed.
3. **Use GraphQL for complex queries** — Item IDs, field IDs, and status options require GraphQL.
4. **Organizations vs Users** — Use `organization(login: "X")` instead of `user(login: "X")` for org-owned repos. Check with `gh api repos/<OWNER>/<REPO> --jq '.owner.type'`.
5. **Browser auth may be needed** — `gh auth login` and `gh auth refresh` may trigger a browser OAuth flow. In non-interactive environments, the CLI prints a one-time code and URL.
6. **Column IDs change** — When updating board columns with `updateProjectV2Field`, all option IDs are regenerated. Always save the new IDs from the mutation response.
7. **Label setup** — If using `tech-debt` labels, create them first: `gh label create "tech-debt" --description "Technical debt" --color "fbca04"`. Default repos only have `bug`, `enhancement`, `documentation`, etc.
8. **Pagination** — `gh issue list` / `gh pr list` / `gh project item-list` default to `--limit 30`, so omitting `--limit` silently returns only 30 items. To fetch the full set, pass a very large `--limit` (e.g. `100000`, which paginates internally) or use `gh api graphql --paginate`. Use `--state`/`--search` filters to narrow scope, not a small `--limit`.
