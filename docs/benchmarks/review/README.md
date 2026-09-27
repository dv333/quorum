# Code-review benchmark

Run 2026-09-26. Cases: `benchmarks/review/cases/`. Method: `scripts/benchmark_review.py`.

## Summary

| Reviewer | Planted bugs found | False alarms on clean changes | Unmatched findings (to judge) | Time | Cost |
|---|---|---|---|---|---|
| lint | 5 of 27 (19%) | 0 | 1 | 0.0 min | $0.00 |
| local | 27 of 27 (100%) | 1 | 1 | 12.9 min | $0.00 |
| claude | 26 of 27 (96%) | 0 | 1 | 3.6 min | $1.10 |
| quorum | 24 of 27 (89%) | 2 | 2 | 230.0 min | $0.00 |

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

**quorum** (Quorum (8 local models: deepseek-r1:8b, gemma3:12b, gpt-oss:20b, llama3.1:8b, phi4:14b, qwen3.6:latest, qwen3.8:latest, qwen3:14b); 16.8 min, conundrum `ae311204c456`, Coder calls: 2): 11 findings
- unmatched (high): **High** · `fetcher.py` · 19‑20 · No URL validation – potential SSRF. · Validate `url.scheme in ('http', 'https')` and reject private IP ranges or use a safe host‑allowlist.
- unmatched (medium): **Medium** · `fetcher.py` · 14 · `retries` is caller‑supplied and can be arbitrarily large. · Clamp to a reasonable maximum (e.g., `retries = min(max(retries, 1), 5)`).

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
| every exception is swallowed and None returned | · | ✓ | ✓ | · |
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

**quorum** (Quorum (8 local models: deepseek-r1:8b, gemma3:12b, gpt-oss:20b, llama3.1:8b, phi4:14b, qwen3.6:latest, qwen3.8:latest, qwen3:14b); 36.1 min, conundrum `dc0748b124e5`, Coder calls: 2): 21 findings

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

**quorum** (Quorum (8 local models: deepseek-r1:8b, gemma3:12b, gpt-oss:20b, llama3.1:8b, phi4:14b, qwen3.6:latest, qwen3.8:latest, qwen3:14b); 12.5 min, conundrum `a83c305ca95c`, Coder calls: 3): 7 findings

## settings: clean change (no bugs)

Task: Move settings into a typed, immutable dataclass, accept true/yes/on for DEBUG, and keep get_settings for existing callers.

**lint** (ruff; 0.0 min): 0 findings

**local** (qwen3.6:latest; 2.3 min): 4 findings
- false alarm (medium): **File:** `config.py` (lines 33 & 35)   **Issue:** Using `Settings.db_url` and `Settings.workers` as fallback values in `load_settings` tightly couples the loader function to the dataclass definition. If defaults are eve

**claude** (Claude Code (claude-haiku-4-5-20251001,claude-opus-5-5); 0.5 min): 7 findings

**quorum** (Quorum (8 local models: deepseek-r1:8b, gemma3:12b, gpt-oss:20b, llama3.1:8b, phi4:14b, qwen3.6:latest, qwen3.8:latest, qwen3:14b); 48.4 min, conundrum `a461a1036541`, Coder calls: 3): 1 findings
- false alarm (high): **High Correctness Risk (Medium Severity):** The widened `DEBUG` parsing now accepts `true`, `yes`, and `on`, which can inadvertently enable debug modes in production; you should add a `logging.warning` when debug is ena

## uploads: 5 planted bugs

Task: Let users keep uploads in folders and choose the thumbnail size.

| Bug | lint | local | claude | quorum |
|---|---|---|---|---|
| save_upload joins unsanitized folder and name (path traversal, overwrite any file) | · | ✓ | ✓ | ✓ |
| read_upload joins unsanitized folder and name (read any file) | · | ✓ | ✓ | · |
| shell=True with the user's size and path (command injection) | ✓ | ✓ | ✓ | ✓ |
| the 30 s timeout on convert was removed | · | ✓ | ✓ | ✓ |
| files are opened without being closed (handle leak, unflushed write) | · | ✓ | ✓ | ✓ |

**lint** (ruff; 0.0 min): 1 findings

**local** (qwen3.6:latest; 1.4 min): 8 findings
- unmatched (medium): 6. Inconsistent Path API Usage **File/Lines:** `read_upload` (~line 25) **Issue:** Uses `os.path.join()` while the rest of the file uses `pathlib.Path`. This mixes APIs, reduces type safety, and can cause subtle cross-pl

**claude** (Claude Code (claude-haiku-4-5-20251001,claude-opus-5-5); 0.6 min): 9 findings

**quorum** (Quorum (8 local models: deepseek-r1:8b, gemma3:12b, gpt-oss:20b, llama3.1:8b, phi4:14b, qwen3.6:latest, qwen3.8:latest, qwen3:14b); 12.4 min, conundrum `875014278b08`, Coder calls: 3): 4 findings

## user-search: clean change (no bugs)

Task: Add a name search for users, with a capped result limit; % and _ typed by users match literally.

**lint** (ruff; 0.0 min): 0 findings

**local** (qwen3.6:latest; 3.0 min): 5 findings

**claude** (Claude Code (claude-haiku-4-5-20251001,claude-opus-5-5); 0.5 min): 8 findings

**quorum** (Quorum (8 local models: deepseek-r1:8b, gemma3:12b, gpt-oss:20b, llama3.1:8b, phi4:14b, qwen3.6:latest, qwen3.8:latest, qwen3:14b); 103.8 min, conundrum `e45d1c1ccaea`, Coder calls: 4): 6 findings
- false alarm (medium): **Medium** · `users.py:12` · `term=None` causes `AttributeError` with no clear guard. · Add `if term is None: raise TypeError("term cannot be None")`.


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
