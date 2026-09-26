"""Replay a saved conundrum against your running Quorum (real models) and check the answer against expectations.

    uv run python scripts/replay.py tests/regressions/cpq-oracle-middleware.json
    uv run python scripts/replay.py --all

Each spec in tests/regressions/ holds a question and what a good answer must (and must not) do. The checks run on the
final answer plus its evidence ledger. A replay takes as long as a normal conundrum; it stays in your history so you
can read it in the app. Exit code 1 when any check fails.
"""

import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend import cli  # noqa: E402  (talks to the running backend over HTTP)

HERE = os.path.dirname(__file__)


def says(text: str, phrase: str) -> bool:
    """Whether text contains the phrase at the start of a word ("OIC" doesn't match inside "choice")."""
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase.lower()), text.lower()) is not None


def check(spec, answer: str, claims: list) -> list:
    """(passed, description) for each expectation."""
    expect = spec["expect"]
    ledger = " ".join(f"{c['claim']} {c.get('caveat', '')} {c.get('quote', '')}" for c in claims)
    text = f"{answer}\n{ledger}".lower()
    results = []
    for phrase in expect.get("mentions_all", []):
        results.append((says(text, phrase), f'mentions "{phrase}"'))
    groups = expect.get("mentions_groups", []) + ([expect["mentions_any"]] if expect.get("mentions_any") else [])
    for options in groups:
        results.append((any(says(text, o) for o in options), "mentions one of: " + ", ".join(options)))
    for phrase in expect.get("never_says", []):
        results.append((not says(answer, phrase), f'the answer never says "{phrase}"'))
    for phrase in expect.get("claims_not_supported", []):
        bad = [c["claim"] for c in claims if phrase.lower() in c["claim"].lower() and c["status"] == "supported"]
        results.append((not bad, f'no "{phrase}" claim is marked supported' + (f" (found: {bad[0]})" if bad else "")))
    return results


def run(path: str, quiet: bool) -> bool:
    with open(path, encoding="utf-8") as f:
        spec = json.load(f)
    print(f"\n▶ {spec['name']}", file=sys.stderr)
    snap = cli.post("/debates", {"question": spec["question"], "research_enabled": True})
    debate_id = snap["debate"]["id"]
    print(f"  conundrum {debate_id} · {cli.APP_URL}/#q/{debate_id}", file=sys.stderr)
    snap = cli.wait_for_answer(debate_id, interactive=False, progress=cli.Progress(quiet))
    topic = snap["debate"]["topic"]
    verdict = next(v for v in snap["verdicts"] if v["topic"] == topic)
    answer = next(m for m in snap["messages"] if m["id"] == verdict["message_id"])["content"]
    claims = [c for c in snap.get("claims", []) if c["topic"] == topic]
    results = check(spec, answer, claims)
    for ok, what in results:
        print(f"  {'✓' if ok else '✗'} {what}")
    statuses = ", ".join(f"{c['status']}: {c['claim'][:70]}" for c in claims) or "none"
    print(f"  evidence ledger: {statuses}")
    passed = all(ok for ok, _ in results)
    print(f"  {'PASS' if passed else 'FAIL'}")
    return passed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("specs", nargs="*", help="regression spec files")
    ap.add_argument("--all", action="store_true", help="run every spec in tests/regressions/")
    ap.add_argument("-q", "--quiet", action="store_true", help="no progress lines")
    args = ap.parse_args()
    specs = args.specs or (
        sorted(glob.glob(os.path.join(HERE, "..", "tests", "regressions", "*.json"))) if args.all else []
    )
    if not specs:
        ap.error("give a spec file or --all")
    try:
        results = [run(s, args.quiet) for s in specs]
    except cli.QuorumError as e:
        print(f"replay: {e}", file=sys.stderr)
        return 1
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
