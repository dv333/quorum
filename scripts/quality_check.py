"""Before a release: does Quorum still answer as well as it did? About 50 minutes on your running Quorum.

Runs 5 debate-benchmark questions and 2 review-benchmark cases on the models you have, then compares with the last
saved result in benchmarks/quality/baseline.json. It fails (exit 1) when Quorum gets 2 or more fewer right on either
part, so a prompt or engine change that makes answers worse is caught before it ships.

    uv run python scripts/quality_check.py                  # check against the baseline
    uv run python scripts/quality_check.py --save-baseline  # after a release you're happy with

Needs Quorum running (./start.sh) with the models you normally use. Scores depend on those models: compare runs on
the same machine and council.
"""

import argparse
import datetime as dt
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "benchmarks" / "quality" / "baseline.json"
# A mix the council has split on: late binding, floor division, a probability, a percentage trap, an age puzzle
DEBATE_IDS = ["py-04", "py-07", "two-children", "up-down", "ages-sum"]
REVIEW_CASES = ["orders", "uploads"]  # the two quickest cases: 19 planted bugs, about 10 minutes together
ALLOWED_DROP = 2


def run(script: str, *args: str) -> None:
    subprocess.run([sys.executable, str(ROOT / "scripts" / script), *args], check=True, cwd=ROOT)


def measure(out: Path) -> Dict[str, Any]:
    debate_out, review_out = out / "debate", out / "review"
    run("benchmark_debate.py", "--ids", ",".join(DEBATE_IDS), "--no-single", "--out", str(debate_out))
    run("benchmark_review.py", "--reviewers", "quorum", "--cases", ",".join(REVIEW_CASES), "--out", str(review_out))
    debate = json.loads((debate_out / "results.json").read_text())
    review = json.loads((review_out / "results.json").read_text())["cases"]
    return {
        "date": dt.date.today().isoformat(),
        "debate_right": sum(bool(debate[i]["quorum"]["final_correct"]) for i in DEBATE_IDS if i in debate),
        "debate_of": len(DEBATE_IDS),
        "review_found": sum(len(review[c]["quorum"].get("score", {}).get("found", [])) for c in REVIEW_CASES),
        "review_errors": [c for c in REVIEW_CASES if "error" in review[c]["quorum"]],
    }


def compare(now: Dict[str, Any], base: Dict[str, Any]) -> list:
    """What got worse by ALLOWED_DROP or more (an empty list: nothing did)."""
    worse = []
    for key, label in (("debate_right", "debate questions right"), ("review_found", "planted bugs found")):
        if base.get(key) is not None and now[key] <= base[key] - ALLOWED_DROP:
            worse.append(f"{label}: {now[key]}, was {base[key]} on {base.get('date', '?')}")
    if now.get("review_errors"):
        worse.append("reviews that failed: " + ", ".join(now["review_errors"]))
    return worse


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--save-baseline", action="store_true", help="save this run as the baseline to compare against")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix="quorum-quality-") as tmp:
        now = measure(Path(tmp))
    print(f"\nDebate: {now['debate_right']} of {now['debate_of']} right · Review: {now['review_found']} bugs found")
    if args.save_baseline or not BASELINE.exists():
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(now, indent=2) + "\n")
        print(f"Saved as the baseline: {BASELINE.relative_to(ROOT)}")
        return 0
    worse = compare(now, json.loads(BASELINE.read_text()))
    for line in worse:
        print(f"WORSE  {line}")
    print("Quality check failed." if worse else "No drop against the baseline.")
    return 1 if worse else 0


if __name__ == "__main__":
    sys.exit(main())
