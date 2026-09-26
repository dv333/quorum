"""The code-review benchmark's scoring: findings split from an answer and matched to the planted bugs."""

import importlib.util

import pytest

spec = importlib.util.spec_from_file_location("benchmark_review", "scripts/benchmark_review.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)

ANSWER = """BOTTOM LINE: Don't merge.

## Key points
- **Errors are swallowed:** every exception is caught and None returned.

## Details
**Must fix (High):**
1. Parameterize `db.py:15` (`customer_id`) with `?` placeholders.
2. Restore `timeout=10` on the request.

**Should fix (Medium):**
- Cache the settings at import time.

**What looks correct:**
- `create_order` uses parameterized queries.
"""

CASE = {
    "bugs": [
        {"id": "sqli", "match": ["parameteri|placeholder", "db\\.py:15|customer_id"]},
        {"id": "timeout", "match": ["timeout"]},
        {"id": "swallowed", "match": ["swallow"]},
        {"id": "http", "match": ["plain http"]},
    ],
    "acceptable": [],
}


def test_findings_come_from_severity_sections_and_skip_what_looks_correct():
    findings = bench.split_findings(ANSWER)
    by_severity = {f["severity"]: [] for f in findings}
    for f in findings:
        by_severity[f["severity"]].append(f["text"])
    assert len(by_severity["high"]) == 2 and len(by_severity["medium"]) == 1
    assert by_severity["summary"] == ["**Errors are swallowed:** every exception is caught and None returned."]
    assert not any("create_order" in f["text"] for f in findings)


def test_bugs_found_anywhere_count_and_unmatched_findings_are_listed():
    s = bench.score(CASE, bench.split_findings(ANSWER))
    assert set(s["found"]) == {"sqli", "timeout", "swallowed"} and s["missed"] == ["http"]
    assert s["recall"] == 0.75 and s["findings"] == 3
    assert [f["text"] for f in s["unmatched"]] == ["Cache the settings at import time."]


def test_on_a_clean_change_high_and_medium_findings_are_false_alarms():
    clean = {"clean": True, "bugs": [], "acceptable": [{"id": "cache", "match": ["cache"]}]}
    findings = [
        {"severity": "high", "text": "SQL injection in search_users"},
        {"severity": "medium", "text": "cache the settings"},
        {"severity": "low", "text": "rename x"},
        {"severity": "summary", "text": "looks risky"},
    ]
    s = bench.score(clean, findings)
    assert [f["text"] for f in s["false_alarms"]] == ["SQL injection in search_users"]
    assert s["recall"] is None


def test_answers_without_severities_count_every_list_item():
    findings = bench.split_findings("Problems:\n- a bug\n- another bug\n")
    assert [f["severity"] for f in findings] == ["medium", "medium"]


@pytest.mark.parametrize("case", [c["name"] for c in bench.load_cases()])
def test_every_case_has_a_task_and_a_base_and_change(case):
    (c,) = bench.load_cases([case])
    assert c["task"] and (c["folder"] / "base").is_dir() and (c["folder"] / "change").is_dir()
    assert c.get("clean") or c["bugs"]
    for point in c["bugs"] + c["acceptable"]:
        assert point["match"], point["id"]


TITLED = """I found two problems.

## High

**1. `get_all` never fetches anything (`fetcher.py:29-33`)**
The coroutine is never awaited.
*Fix:* use gather.

**2. Running out of retries caches an error page**
- **Last attempt got an HTTP error:** the body is cached.
- **Every attempt failed:** `UnboundLocalError`.

## Medium

**3. No tests**
- a request that fails twice then succeeds
"""


def test_titled_findings_keep_their_paragraphs_and_sub_bullets():
    findings = bench.split_findings(TITLED)
    assert [f["severity"] for f in findings] == ["high", "high", "medium"]
    assert "never awaited" in findings[0]["text"] and "use gather" in findings[0]["text"]
    assert "UnboundLocalError" in findings[1]["text"]
    assert findings[2]["text"].startswith("3. No tests") and "fails twice" in findings[2]["text"]


TABLE = """## Details

### High-severity fixes
| File | Line | Issue | Fix |
|------|------|-------|-----|
| `orders/db.py` | 13-18 | `WHERE customer_id = %s` built via string interpolation | Use `?` placeholders. |
| `orders/api.py` | 16 | Route no longer typed | Revert to `<int:customer_id>`. |

### Correct aspects
| What | Why |
|---|---|
| create_order | parameterized |
"""


def test_table_rows_are_findings():
    findings = bench.split_findings(TABLE)
    assert [f["severity"] for f in findings] == ["high", "high"]
    assert findings[0]["text"].startswith("`orders/db.py` · 13-18 · `WHERE customer_id")


def test_bold_titles_with_text_on_the_same_line_are_findings():
    answer = (
        "## High: must fix before merge\n\n"
        "**1. Live secret committed**: `orders/payments.py:8`\n**Fix:** Rotate it.\n\n"
        "**2. Retries can double-charge**: `orders/payments.py:12-21`\n- no idempotency key\n- fixed 0.1s sleep\n"
    )
    findings = bench.split_findings(answer)
    assert [f["severity"] for f in findings] == ["high", "high"]
    assert "payments.py:8" in findings[0]["text"] and "Rotate it" in findings[0]["text"]
    assert "idempotency" in findings[1]["text"] and "0.1s" in findings[1]["text"]


def test_code_comments_are_not_headings_and_long_severity_headings_are_sections():
    answer = (
        "### 🔴 High Severity (Bugs, Security, or Data-Loss Risks)\n\n"
        "1. **SQL injection**\n   - **Fix:**\n     ```python\n     # get_customer_orders\n     x = 1\n     ```\n\n"
        "2. **Hardcoded key**\n   - over plain http\n\n"
        "### 🟡 Medium Severity (Should Be Fixed)\n\n1. **N+1 queries**\n"
    )
    findings = bench.split_findings(answer)
    assert [(f["severity"], f["text"][:20]) for f in findings] == [
        ("high", "**SQL injection** **"),
        ("high", "**Hardcoded key** ov"),
        ("medium", "**N+1 queries**"),
    ]
