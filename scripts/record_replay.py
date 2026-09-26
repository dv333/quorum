"""Record a finished conundrum for the replay demo (#demo in the app), which needs no backend, models or keys.

    uv run python scripts/record_replay.py <conundrum id> --name code-review --label "A code review" --short "Code review"

Saves the conundrum's final state to frontend/public/demos/<name>.json and lists it in index.json. The Simple and
Expert versions of the answer are written first (if they aren't already), so the replay can switch reading levels.
Local paths are replaced (your home folder becomes ~, a reviewed repository becomes repo/), key-shaped strings are
replaced with a fake one, and the models' thinking is left out, which keeps a recording small.
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend import cli  # noqa: E402

DEMOS = Path(__file__).resolve().parent.parent / "frontend" / "public" / "demos"


def scrub(text: str, repo: str) -> str:
    """Replace local paths: the reviewed repository with repo/, then the home folder with ~."""
    if repo:
        for form in {repo, os.path.realpath(repo), repo.replace("/private/var/", "/var/")}:
            text = text.replace(form.rstrip("/") + "/", "repo/").replace(form.rstrip("/"), "repo")
    text = re.sub(r"(/private)?/var/folders/[\w/+-]+?/quorum-bench-[\w-]+", "repo", text)
    # key-shaped strings (even a review's planted fake key) would trip secret scanning when the recording is pushed
    text = re.sub(r"\b(sk|rk|pk)_live_[A-Za-z0-9]{8,}", "live_secret_3f9a1c7e2b8d4f60a5c9e1b7d2f4a6c8", text)
    return text.replace(str(Path.home()), "~")


def record(debate_id: str) -> dict:
    snap = cli.get(f"/debates/{debate_id}")
    verdict = cli._verdict(snap, snap["debate"]["topic"])
    if not verdict:
        raise SystemExit(f"Conundrum {debate_id} has no answer yet.")
    versions = {}
    for level in ("simple", "expert"):
        print(f"writing the {level} answer…", flush=True)
        versions[level] = cli.post(f"/debates/{debate_id}/verdicts/{verdict['id']}/level", {"level": level})["content"]
    for m in snap["messages"]:
        m["thinking"] = ""
    repo = snap["debate"].get("repo_path") or ""
    snap["debate"]["repo_path"] = "repo" if repo else None
    text = scrub(json.dumps({"snapshot": snap, "versions": versions}, ensure_ascii=False), repo)
    return json.loads(text)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("id", help="the conundrum's id (from its link: #q/<id>)")
    ap.add_argument("--name", required=True, help="file name, used in the link: #demo/<name>")
    ap.add_argument("--label", required=True, help='what it is, shown in the replay bar, e.g. "A code review"')
    ap.add_argument("--short", required=True, help="tab label when there are several recordings")
    args = ap.parse_args()

    recording = record(args.id)
    recording.update(name=args.name, label=args.label, recorded=dt.date.today().strftime("%b %Y"))
    DEMOS.mkdir(parents=True, exist_ok=True)
    path = DEMOS / f"{args.name}.json"
    path.write_text(json.dumps(recording, ensure_ascii=False, separators=(",", ":")))
    index_path = DEMOS / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else []
    index = [d for d in index if d["name"] != args.name] + [
        {"name": args.name, "label": args.label, "short": args.short}
    ]
    index_path.write_text(json.dumps(index, indent=2) + "\n")
    print(f"saved {path} ({path.stat().st_size // 1024} KB). Open http://localhost:5173/#demo/{args.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
