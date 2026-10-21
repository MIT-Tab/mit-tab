---
name: mittab-task
description: Implement mit-tab changes using isolated worktrees, focused tests, dev screenshots, PRs, and quiet CI watching. Use for feature, fix, and maintenance work in this repository.
---

# mit-tab task workflow

Use this versioned skill in any clone or worktree. It is shared by Codex
(`.agents/skills`) and Claude Code (`.claude/skills/mittab-task`, a relative symlink).
Follow explicit user scope; create/push a PR when requested or already authorized.

From inside the checkout, resolve the helpers once:

```bash
S="$(git rev-parse --show-toplevel)/.agents/skills/mittab-task/scripts"
```

## Start

Run `"$S/start-task" <kebab-case-slug>`. It fetches `origin/master`, creates a
**sibling of the current checkout** on `codex/<slug>`, links its `.env` if present,
and installs dependencies with `uv sync --all-groups` and `npm ci`. Never edit the
starting checkout: it may contain the user's uncommitted work. If already working
in a dedicated task worktree, continue there instead of nesting worktrees.
After changing into the printed path, resolve `S` again to use that worktree's helpers.

Prerequisites: Git, Bash, uv, Node/npm; authenticated `gh` for PR/CI operations.
For DB work, follow the platform setup in `CONTRIBUTING.md`: running MySQL, mysql
CLI, and a configured `.env` (shell-compatible assignments) or exported environment.
The DB user needs CREATE/DROP privileges for private dev/test databases. Helpers
isolate databases and state by checkout path, including differently named clones.
No credentials, browser caches, or global skill installation are shipped with the repo.
Windows developers should use WSL as described in CONTRIBUTING.md.

## Test

- `"$S/mt-test" [pytest args]` uses a private DB, saves full output, and prints a
  short summary or failure tails. No arguments runs CI's non-browser suite.
  Narrow while iterating, e.g. `"$S/mt-test" mittab/apps/registration -x`.
- `"$S/mt" <command...>` loads the worktree DB environment, e.g.
  `"$S/mt" uv run python manage.py makemigrations --check --dry-run`.
- Before handoff run
  `uv run python bin/check-dependency-age && uv run pylint mittab && npm run lint`.

## Dev server and screenshots

For changes with visible UI, run `"$S/dev-up"`. It migrates the private dev DB,
loads `testing_db.json` on first use, starts webpack and Django, and prints the URL
and log paths. Login **tab / password**. `--fresh` rebuilds only this checkout's
DB; `--empty` uses initialize_tourney. Stop with `"$S/dev-down"`.

`"$S/shot" [--login] [--mobile] [--full] [--wait CSS] [--click CSS]
[--element CSS] [--js JS] PATH OUT.png [PATH OUT.png ...]` captures pages.
Save PR images to `docs/img/<slug>/`; run separately for mobile and desktop.
Selenium Manager finds/downloads Chrome and its driver on first use (network
required). Install Chrome's system libraries for your platform if it cannot start.
`MT_CHROME_LIBS` can point to an optional local Linux library directory.
View the printed small preview JPEG once at default detail; keep full PNGs for PRs.
Skip dev server/screenshots for changes without visible UI.

## PR

Commit the implementation. For UI screenshots run
`"$S/pr-images" docs/img/<slug>/*.png`; it commits those images, pushes, and prints
commit-pinned Markdown image links. Otherwise push with `git push -u origin HEAD`.
Write the PR body to a file and run:

```bash
gh pr create -R MIT-Tab/mit-tab --base master --head "$(git branch --show-current)" \
  --title "..." --body-file <file>
```

## CI: one watcher, no agent polling

The helper polls GitHub quietly at 60-second intervals and prints once when the
current PR head's checks finish, including failing CircleCI log tails. It has no
Python dependencies beyond the standard library; invoke with `uv run python`.

- **Claude Code, or Codex with background task completion notifications:** run
  `uv run python "$S/ci-watch"` once using the execution tool's background-task
  mode (Claude: `run_in_background: true`). End the turn and wait for that tool's
  completion notification. Do not repeatedly read its output or task status.
- **Codex with `CODEX_THREAD_ID` and a CLI supporting `codex queue`:** run
  `uv run python "$S/ci-watch" --detach` and end the turn. The watcher queues the
  result to that explicit thread; `--thread <id>` may also be provided. Never
  guess another session ID. If `codex` is not on PATH or lacks `queue`, use the
  background-task method above instead.
- **Plain terminal / no agent notifications:** run the watcher once in the
  foreground. `--detach` without a thread instead saves a `.result` file at the
  printed path; it cannot wake an agent. Report this limitation rather than
  promising a notification or starting a polling loop.

On failure, read the reported logs, reproduce locally, fix and push, then start
one new watcher. On success, give the final summary and PR link. Do not call
`gh pr checks`, manually poll, or run a second watcher alongside this one.

## Keep context small

Save verbose command output to logs; print summaries and failure tails. Give
long-running exec calls a 30-second initial wait where supported. Do not repeatedly
poll running tools. Read narrow file ranges and targeted diffs, avoid re-reading
unchanged files, and do not re-run passing checks without a relevant change.
