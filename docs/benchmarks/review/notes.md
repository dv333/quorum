## Notes from checking by hand

- **Quorum's misses were dropped in the summary, not missed by the council.** In the orders review
  (`dc0748b124e5`), Panda and Koala raised the removed `<int:>` converter and Panda and Hedgehog the swallowed
  exceptions in round 1; the chair's answer left both out. In the uploads review (`875014278b08`), `read_upload`
  traversal came up 10 times in the debate and appears in the answer's fix plan, but not in its findings. Making the
  answer keep every finding an agent raised, unless it's ruled out, is the next change to reviews.
- **The uploads review first ran as a "direct" answer** (`c1b977742a9d`): the chair took the diff for a simple
  question and replied "Ready to get started?". Code reviews now always go to the council (#25); the result above is
  the rerun.
- **False alarms.** Quorum on settings rated the requested change (DEBUG accepting true/yes/on) a High risk; on
  user-search it rated a missing `None` guard Medium. The single local model on settings rated a design preference
  (defaults read from the dataclass) Medium. Claude Code's point that the limit cap is never really tested is correct
  and isn't counted.
- **Unmatched findings on changes with bugs** were checked: SSRF on fetcher (the fetcher takes URLs by design; a fair
  hardening note), ruff's unused loop variable, and duplicates of bugs already found in other words.
- **One run per case.** Model output varies between runs; a difference of one or two bugs is within that noise. The
  orders case earlier used a Stripe-shaped fake key, which GitHub's secret scanning blocks, so it was rerun with an
  obviously fake key; all numbers above are from the published cases.
