## Notes from checking by hand

- **Quorum was rerun** after three changes to reviews: the answer keeps every finding an agent raised
  (#27), turns have time and length limits and thinking only in round 1 (#29), and the check for dropped findings is
  one short call (#31). Lint, the local model and Claude Code are from the first run; their scores are unchanged
  by the scoring fixes below.
- **Before these changes** Quorum found 24 of 27 bugs with 2 false alarms, taking 38 minutes per review on average.
  Its misses were raised in the debate and dropped from the answer.
- **Rounds.** The chair sizes each debate: it gave orders and uploads 1 round and the other cases 3. Times are
  comparable only for the 3-round reviews: fetcher 16.8 → 10.5 min, pagination 12.5 → 10.0, settings 48.4 → 11.4 and
  user-search 103.8 → 11.2.
- **The one miss** (orders, the removed `<int:>` route converter) was raised by the Coder and by Panda in round 1, and
  left out of the answer; the check for dropped findings didn't bring it back.
- **False alarms** are all on user-search. Two were in the chair's answer: a missing `None` guard for `term` (the
  function takes a string) and `ORDER BY name, id` (a nit presented as a fix). The third was added back by the check
  for dropped findings: Panda rated non-numeric `limit` values High; the point is fair at Medium, but High claims a
  must-fix bug on a clean change. The single local model on settings rated a design preference (defaults read from
  the dataclass) Medium.
- **Scoring fixes in this run.** Numbered bold titles with sub-bullets (`1. **[High] Title:**`) are now read as one
  finding, and a bold label like `**Code fixes:**` as a group; "cannot test" counts as a test-coverage point. The case
  repositories had picked up compiled Python files (`__pycache__/`) from running the cases' tests, so every reviewer
  saw them in the settings and user-search diffs; only Quorum mentioned them, rightly, so that isn't counted as a
  false alarm. The repositories now leave them out.
- **Unmatched findings on changes with bugs** were checked: SSRF on fetcher (the fetcher takes URLs by design; a fair
  hardening note), ruff's unused loop variable, and duplicates of bugs already found in other words.
- **One run per case.** Model output varies between runs; a difference of one or two bugs is within that noise.
