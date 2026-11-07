# Wasted Wind — rules for all agents
Project: reconstruct, forecast, dispatch and simulate Scottish wind curtailment.
Brief: see docs/BRIEF.md. Data formats and API shapes: docs/CONTRACT.md (never change without a PR).

## Who owns what
- `owner:A-data` (aradhyak0, agent "data"): `pipeline/`, `dispatch/`, `reference/`, `docs/samples/`.
- `owner:B-app` (parlink, agent "app"): `app/`, `sim/`, repo setup (`Makefile`, `.github/`, `.claude/`).
- Shared (both humans approve): `CLAUDE.md`, `docs/CONTRACT.md`, `requirements.txt`, `tests/` for your own folders only.

## How we work
- Start of session: `gh issue list --label <your owner label> --state open`. Pick the oldest core-tier issue.
- Comment "Starting" on the issue. Work on branch <a|b>/<issue-number>-<slug>.
- Write the test first, then the code. Run `pytest -q` before every commit.
- Finish: open a PR into `master` with "Closes #N" and a 3-line summary. Then comment on the issue.
- Need something from the other side? Open an issue with their owner label, the exact
  file/function/columns you need, and an example. Never edit their folders.
- Stuck for 2 attempts? Label the issue `needs-human` and stop.
- Tier order: no `tier:full` work until Core works end to end; no `tier:stretch` until Full is merged.

## Rules
- Parse API data against real samples in docs/samples/. Never invent field names.
- Times are UTC. Energy in MWh, power in MW. Say which in every column name.
- Never read files in data/. Inspect data with short scripts.
- Cheap bulk jobs (fixtures, docstrings, summarising docs): run `tools/ask_gemini.sh "<task>"`
  and review its output before using it.
- The default branch is `master` (what we demo from). Never push straight to it after setup.
