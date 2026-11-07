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
- Finish: open a PR into `master` with "Closes #N" and a 3-line summary, requesting the other
  person as reviewer (`gh pr create --reviewer <aradhyak0|parlink>`). Then comment on the issue.
- Need something from the other side? Open an issue with their owner label, the exact
  file/function/columns you need, and an example. Never edit their folders.
- Stuck for 2 attempts? Label the issue `needs-human` and stop.

## Review loop (run by `/loop 15m Run one pass of the Review loop in CLAUDE.md`)
One pass, then stop. Never check out another branch in your working folder: review from
`gh pr diff N` and let CI run the tests. If there is nothing to do, reply "nothing to do".
1. List: `gh pr list --state open --json number,title,author,labels,reviewDecision,files`.
2. **Other person's PRs** (skip any labelled `needs-human`):
   - Touches `docs/CONTRACT.md` or `CLAUDE.md`? Add label `needs-human`, comment "Shared file:
     needs both humans to approve", and skip it. Agents never approve these.
   - CI still running (`gh pr checks N`)? Skip until the next pass. CI failing? Request changes
     quoting the failing test.
   - Otherwise read `gh pr diff N`. For `pipeline/`, `dispatch/` or `sim/` code, run the
     `quant-reviewer` subagent on the diff. Check the PR against `docs/CONTRACT.md` and the issue's
     "Done when" list.
   - Approve (`gh pr review N --approve --body "<what you checked>"`) only if CI is green and there
     are no blocking problems. Otherwise `gh pr review N --request-changes --body "<numbered list>"`.
     Style nits go in a comment, not a block.
   - Already reviewed and no new commits since? Skip.
3. **Your own PRs**:
   - Changes requested: fix them on the PR's branch, push, reply to each point.
   - Approved, CI green, no `needs-human`: `gh pr merge N --squash --delete-branch`, then
     `git checkout master && git pull` and comment on the issue.
   - After 2 rounds of requested changes on the same PR, label it `needs-human` and stop.
4. **Issues** with your owner label that have new comments from the other side: answer or act.

## Rules
- Parse API data against real samples in docs/samples/. Never invent field names.
- Times are UTC. Energy in MWh, power in MW. Say which in every column name.
- Never open or print whole files in data/. Inspect with short scripts (head, describe).
- Cheap bulk jobs (fixtures, docstrings, summarising docs): run `tools/ask_gemini.sh "<task>"`
  and review its output before using it.
- The default branch is `master` (what we demo from). Never push straight to it after setup.
- Finish all tier:core issues before starting any tier:full issue; no tier:stretch until all
  tier:full issues are merged.
