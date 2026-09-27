# Code-review benchmark

Run 2026-09-26. Cases: `benchmarks/review/cases/`. Method: `scripts/benchmark_review.py`.

## Summary

| Reviewer | Planted bugs found | False alarms on clean changes | Unmatched findings (to judge) | Time | Cost |
|---|---|---|---|---|---|
| lint | 5 of 27 (19%) | 0 | 1 | 0.0 min | $0.00 |
| local | 27 of 27 (100%) | 1 | 1 | 12.9 min | $0.00 |
| claude | 26 of 27 (96%) | 0 | 1 | 3.6 min | $1.10 |
| quorum | 26 of 27 (96%) | 3 | 7 | 53.6 min | $0.00 |

Cost is API spend; local models cost $0 beyond electricity. Time is wall-clock for all cases.

## fetcher: 4 planted bugs

Task: Retry failed fetches with exponential backoff and log how many URLs were fetched.

| Bug | lint | local | claude | quorum |
|---|---|---|---|---|
| requests no longer go through the semaphore (unbounded concurrency) | · | ✓ | ✓ | ✓ |
| time.sleep blocks the event loop in async code | ✓ | ✓ | ✓ | ✓ |
| when every retry fails, the error response is cached and returned as success (or resp is unbound) | · | ✓ | ✓ | ✓ |
| get_all returns un-awaited coroutines (no fetch ever runs) | · | ✓ | ✓ | ✓ |

**lint** (ruff; 0.0 min): 1 findings

**local** (qwen3.6:latest; 1.3 min): 9 findings

**claude** (Claude Code (claude-haiku-4-5-20251001,claude-opus-5-5); 0.5 min): 10 findings

**quorum** (Quorum (5 local models: gemma3:12b, gpt-oss:20b, phi4:14b, qwen3.6:latest, qwen3:14b); 10.5 min, conundrum `8c8f6a8e1097`, Coder calls: 4): 4 findings
- unmatched (medium): **[low] The HTTP client does not set a User‑Agent header, increasing the risk of being blocked by strict APIs.** (fetcher.py:7), raised by Panda: Many servers reject requests without a proper User-Agent string. Fix: Add 

## orders: 14 planted bugs

Task: Add order search, response caching, background charging and payment retries.

| Bug | lint | local | claude | quorum |
|---|---|---|---|---|
| customer_id is %-formatted into SQL (db.py:15) | ✓ | ✓ | ✓ | ✓ |
| the search term is f-stringed into a LIKE query (db.py:25) | ✓ | ✓ | ✓ | ✓ |
| one customer query per order (N+1) | · | ✓ | ✓ | ✓ |
| a live API key is hardcoded | · | ✓ | ✓ | ✓ |
| the payments URL is plain http | · | ✓ | ✓ | ✓ |
| requests.post has no timeout | ✓ | ✓ | ✓ | ✓ |
| 10 retries, fixed 0.1 s sleep, no backoff or status check | · | ✓ | ✓ | ✓ |
| every exception is swallowed and None returned | · | ✓ | ✓ | ✓ |
| the order is marked paid even when the charge failed (None) | · | ✓ | ✓ | ✓ |
| one SQLite connection shared across threads (check_same_thread=False) | · | ✓ | ✓ | ✓ |
| the global _cache is read and cleared across threads without a lock | · | ✓ | · | ✓ |
| charging runs in an untracked background thread (lost on crash, no result) | · | ✓ | ✓ | ✓ |
| the <int:> route converter was removed | · | ✓ | ✓ | · |
| response fields renamed (id/total to order_id/amount), breaking clients | · | ✓ | ✓ | ✓ |

**lint** (ruff; 0.0 min): 5 findings
- unmatched (medium): orders/payments.py:12 B007 Loop control variable `attempt` not used within loop body

**local** (qwen3.6:latest; 1.9 min): 14 findings

**claude** (Claude Code (claude-haiku-4-5-20251001,claude-opus-5-5); 0.9 min): 20 findings
- unmatched (high): Fix: Either keep charging synchronous, or insert the order with `status='pending_payment'` and hand the charge to a durable queue (RQ, Celery, or an outbox table plus a worker). Record `paid` or `payment_failed` explicit

**quorum** (Quorum (5 local models: gemma3:12b, gpt-oss:20b, phi4:14b, qwen3.6:latest, qwen3:14b); 5.7 min, conundrum `94cb091abd07`, Coder calls: 1): 12 findings
- unmatched (medium): BOTTOM LINE:: The code change introduces several critical security and correctness issues that must be fixed before it can be merged; priority should be securing the database queries, payment integration, and concurrency

## pagination: 4 planted bugs

Task: Cap page size at 100 and return whether another page follows.

| Bug | lint | local | claude | quorum |
|---|---|---|---|---|
| start = page * per_page skips the first page (pages start at 1) | · | ✓ | ✓ | ✓ |
| the page < 1 check was removed | · | ✓ | ✓ | ✓ |
| has_next is true when end == len(items), with no next page | · | ✓ | ✓ | ✓ |
| page_count drops the last partial page (floor division) | · | ✓ | ✓ | ✓ |

**lint** (ruff; 0.0 min): 0 findings

**local** (qwen3.6:latest; 3.0 min): 6 findings

**claude** (Claude Code (claude-haiku-4-5-20251001,claude-opus-5-5); 0.6 min): 9 findings

**quorum** (Quorum (5 local models: gemma3:12b, gpt-oss:20b, phi4:14b, qwen3.6:latest, qwen3:14b); 10.0 min, conundrum `1d2efad89e03`, Coder calls: 4): 3 findings

## settings: clean change (no bugs)

Task: Move settings into a typed, immutable dataclass, accept true/yes/on for DEBUG, and keep get_settings for existing callers.

**lint** (ruff; 0.0 min): 0 findings

**local** (qwen3.6:latest; 2.3 min): 4 findings
- false alarm (medium): **File:** `config.py` (lines 33 & 35)   **Issue:** Using `Settings.db_url` and `Settings.workers` as fallback values in `load_settings` tightly couples the loader function to the dataclass definition. If defaults are eve

**claude** (Claude Code (claude-haiku-4-5-20251001,claude-opus-5-5); 0.5 min): 7 findings

**quorum** (Quorum (5 local models: gemma3:12b, gpt-oss:20b, phi4:14b, qwen3.6:latest, qwen3:14b); 11.4 min, conundrum `8f3567c9af81`, Coder calls: 4): 1 findings

## uploads: 5 planted bugs

Task: Let users keep uploads in folders and choose the thumbnail size.

| Bug | lint | local | claude | quorum |
|---|---|---|---|---|
| save_upload joins unsanitized folder and name (path traversal, overwrite any file) | · | ✓ | ✓ | ✓ |
| read_upload joins unsanitized folder and name (read any file) | · | ✓ | ✓ | ✓ |
| shell=True with the user's size and path (command injection) | ✓ | ✓ | ✓ | ✓ |
| the 30 s timeout on convert was removed | · | ✓ | ✓ | ✓ |
| files are opened without being closed (handle leak, unflushed write) | · | ✓ | ✓ | ✓ |

**lint** (ruff; 0.0 min): 1 findings

**local** (qwen3.6:latest; 1.4 min): 8 findings
- unmatched (medium): 6. Inconsistent Path API Usage **File/Lines:** `read_upload` (~line 25) **Issue:** Uses `os.path.join()` while the rest of the file uses `pathlib.Path`. This mixes APIs, reduces type safety, and can cause subtle cross-pl

**claude** (Claude Code (claude-haiku-4-5-20251001,claude-opus-5-5); 0.6 min): 9 findings

**quorum** (Quorum (5 local models: gemma3:12b, gpt-oss:20b, phi4:14b, qwen3.6:latest, qwen3:14b); 4.7 min, conundrum `e37f388b61bd`, Coder calls: 1): 16 findings
- unmatched (medium): **Lack of `safe` extraction for path components, which may contain dangerous characters leading to security risks.** 
- unmatched (medium): *Location*: `uploads.py:16-20` 
- unmatched (medium): **Deprecated `convert` usage may bypass environment security checks by using the shell.** 
- unmatched (medium): *Location*: `uploads.py:28` 
- unmatched (medium): *Location*: `uploads.py:33` 

## user-search: clean change (no bugs)

Task: Add a name search for users, with a capped result limit; % and _ typed by users match literally.

**lint** (ruff; 0.0 min): 0 findings

**local** (qwen3.6:latest; 3.0 min): 5 findings

**claude** (Claude Code (claude-haiku-4-5-20251001,claude-opus-5-5); 0.5 min): 8 findings

**quorum** (Quorum (5 local models: gemma3:12b, gpt-oss:20b, phi4:14b, qwen3.6:latest, qwen3:14b); 11.2 min, conundrum `2a0921051cd1`, Coder calls: 4): 10 findings
- false alarm (high): [High] Validate Limit Type (users.py:13): Converting the limit argument with `int(limit)` can raise a `ValueError` or `TypeError` if a non‑numeric string is passed (e.g., "abc" or "10.5"), leading to an unhandled excepti
- false alarm (medium): **Add None Guard:** Insert `if term is None: return []` at the start of `search_users`. This prevents crashes and clearly defines "no search" as "empty results," which is safer than implicitly treating it as an empty str
- false alarm (medium): **Fix Sorting:** Change `ORDER BY name` to `ORDER BY name, id` to ensure deterministic ordering when names collide.


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
