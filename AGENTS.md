- Environment uses `uv`. Run Python commands with `uv run ...` and install dependencies with `uv sync --all-groups`.
- Before handing off changes, run `uv run python bin/check-dependency-age`, `uv run pylint mittab`, and `npm run lint`.
- Dependency updates and additions must choose direct Python/NPM dependency versions released at least 30 days ago and no more than 913 days ago. The dependency age check runs as its own lint-step check, blocks new or changed direct dependency versions outside that window, and leaves pre-existing out-of-window versions as warnings.
- Read failing CI with `gh pr list -R MIT-Tab/mit-tab --json number,title,statusCheckRollup` and CircleCI build logs from the failing `targetUrl`, e.g. `curl -fsSL https://circleci.com/api/v1.1/project/github/MIT-Tab/mit-tab/<build_num>`.
- For every implementation task, read and follow `.agents/skills/mittab-task/SKILL.md` before changing files. Use this repo copy even if an older user-level `mittab-task` skill is installed.
- Never manually poll CI. Use the skill's CI watcher once, with the agent's background completion mechanism, then end the turn. See the skill for Codex and Claude instructions.

## Board access invariants

- Board access opens at the chronological end of inrounds: once outrounds exist, or at least 80% of final-inround outcomes are entered. Do not require complete ballot/score data, and do not open access merely because the final round was paired.
- Board must never receive additional scratch or judge-calibration information beyond public pairings. Actual assigned judge identities, public chair roles, and structured manual assignment history are allowed. Scratch existence/counts/history, judge eligibility lists, conflict reasons, judge ranks, wing-only flags and calibration-derived ordering are forbidden in responses, errors, exports, audit records, and alternate entrypoints.
- Inround pairings and results are read-only for board. Only outround teams/sides/rooms/outcomes/choice, judge is_dino, and the existing APDA IDs/publication controls may be edited.
- Board must not access Django admin, backups (including scratch-free backups), raw archives, profiler/debug tools, team portals, or judge-code portals.
- Board outround commands may operate on teams, sides, rooms, and results only. They must never call judge pairing/eligibility logic or alter/delete existing judge assignments or chairs, including indirectly through round deletion.
- Use explicit server-side route/method and field allowlists. Broader staff permissions must not override board restrictions. Do not pass unrestricted Judge, Scratch, or AuditEvent objects to board templates or serializers.
- Access expansions must include negative authorization tests, response-schema tests, tests that changing protected data cannot change board responses for otherwise identical allowed state, and tests that board mutations leave protected state unchanged.
- The implemented behavior and security boundary are documented in [docs/Board-Access.md](docs/Board-Access.md).
