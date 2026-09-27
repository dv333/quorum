"""Code-review benchmark: Quorum against one strong model and a linter, on changes with planted bugs and clean changes.

    uv run python scripts/benchmark_review.py                          # every case, every reviewer
    uv run python scripts/benchmark_review.py --reviewers lint,local   # no Quorum or Claude runs
    uv run python scripts/benchmark_review.py --quorum-id orders=827b83249e78   # score a review that already ran
    uv run python scripts/benchmark_review.py --rescore docs/benchmarks/review/results.json

Each case in benchmarks/review/cases/<name>/ has the code before the change (base/), the changed files (change/) and
case.json: the task, the planted bugs and other points that are true but weren't planted ("acceptable"). The script
builds a throwaway git repository per case with the change uncommitted, gives every reviewer the same review request
(the one quorum_review sends), splits each answer into findings and matches them to the planted bugs with the
case's patterns.

Reviewers:
  lint    ruff with its bug, security and async rules (F, B, S, ASYNC, E9)
  local   the largest local model, one call (the same model class Quorum's council is made of)
  claude  Claude Code, read-only, with the repository (a strong single cloud model)
  quorum  a standard Quorum review with the repository attached (the Coder joins when Claude Code or Codex is set up)

Findings are the list items under High, Medium and Low headings (or starting with those words). A planted bug counts
as found when any finding matches it, at any severity. On clean cases, every High or Medium finding that isn't an
"acceptable" point is a false alarm. On cases with bugs, High and Medium findings that match nothing are listed as
"unmatched" for a person to judge, since a reviewer can find real problems nobody planted. A High finding on a clean
change is always a false alarm: High means a must-fix bug, and there is none. The report shows the
finding behind every match, so each one can be checked by hand.

Run it where the reviewers can sign in: Claude Code and the Coder need your Claude login, so start Quorum (./start.sh)
and this script from a normal terminal.
"""

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend import cli, mcp_server  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "benchmarks" / "review" / "cases"
OUT = ROOT / "docs" / "benchmarks" / "review"
REVIEWERS = ["lint", "local", "claude", "quorum"]
OLLAMA = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
LINT_RULES = "F,B,S,ASYNC,E9"

# ------------------------------------------------------------------ cases


def load_cases(names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    cases = []
    for folder in sorted(p for p in CASES.iterdir() if (p / "case.json").exists()):
        if names and folder.name not in names:
            continue
        case = json.loads((folder / "case.json").read_text())
        case.update(name=folder.name, folder=folder)
        cases.append(case)
    return cases


def build_repo(case: Dict[str, Any], into: Path) -> Path:
    """A git repository with the base committed and the change on top, uncommitted."""

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(into), *args], check=True, capture_output=True)

    skip = shutil.ignore_patterns("__pycache__", "*.pyc")  # left by running a case's tests; not part of the change
    shutil.copytree(case["folder"] / "base", into, dirs_exist_ok=True, ignore=skip)
    git("init", "-q")
    git("config", "user.email", "benchmark@example.com")
    git("config", "user.name", "Benchmark")
    git("add", ".")
    git("commit", "-qm", "base")
    shutil.copytree(case["folder"] / "change", into, dirs_exist_ok=True, ignore=skip)
    git("add", "-N", ".")  # new files show up in the diff
    return into


def changed_files(repo: Path) -> List[str]:
    out = subprocess.run(["git", "-C", str(repo), "diff", "--name-only"], capture_output=True, text=True).stdout
    return [f for f in out.split() if f.endswith(".py")]


def request(case: Dict[str, Any], repo: Path) -> str:
    diff = subprocess.run(["git", "-C", str(repo), "diff"], capture_output=True, text=True).stdout
    return mcp_server.review_question(diff, "", "all", case["task"])


# ------------------------------------------------------------------ reviewers


def run_lint(case: Dict[str, Any], repo: Path) -> Dict[str, Any]:
    start = time.monotonic()
    proc = subprocess.run(
        [
            *("uvx", "ruff", "check", "--isolated", "--select", LINT_RULES, "--per-file-ignores", "tests/*:S101"),
            *("--output-format", "json", *changed_files(repo)),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    items = [
        {
            "severity": "medium",
            "text": f"{Path(d['filename']).relative_to(repo)}:{d['location']['row']} {d['code']} {d['message']}",
        }
        for d in json.loads(proc.stdout or "[]")
    ]
    return {"findings": items, "seconds": round(time.monotonic() - start, 1), "cost_usd": 0.0, "model": "ruff"}


def largest_local_model() -> str:
    with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=10) as r:
        models = json.load(r)["models"]
    return max(models, key=lambda m: m.get("size", 0))["name"]


def run_local(case: Dict[str, Any], repo: Path, model: str) -> Dict[str, Any]:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": request(case, repo)}],
        "stream": False,
        "options": {"num_ctx": 16384, "temperature": 0.2},
    }
    start = time.monotonic()
    req = urllib.request.Request(f"{OLLAMA}/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1800) as r:
        answer = json.load(r)["message"]["content"]
    return {"answer": answer, "seconds": round(time.monotonic() - start, 1), "cost_usd": 0.0, "model": model}


def run_claude(case: Dict[str, Any], repo: Path) -> Dict[str, Any]:
    cmd = ["claude", "-p", "--allowedTools", "Read,Grep,Glob", "--output-format", "json", "--max-turns", "20"]
    start = time.monotonic()
    proc = subprocess.run(cmd, input=request(case, repo), cwd=repo, capture_output=True, text=True, timeout=1800)
    out = json.loads(proc.stdout or "{}")
    if proc.returncode or out.get("is_error"):
        raise RuntimeError((out.get("result") or proc.stderr or proc.stdout)[:300])
    return {
        "answer": out.get("result", ""),
        "seconds": round(time.monotonic() - start, 1),
        "cost_usd": round(out.get("total_cost_usd") or 0.0, 4),
        "model": "Claude Code (" + ",".join(sorted((out.get("modelUsage") or {}).keys())) + ")",
    }


def quorum_answer(debate_id: str) -> Dict[str, Any]:
    snap = cli.get(f"/debates/{debate_id}")
    verdict = cli._verdict(snap, snap["debate"]["topic"])
    if not verdict:
        raise RuntimeError(f"conundrum {debate_id} has no answer yet")
    answer = next(m for m in snap["messages"] if m["id"] == verdict["message_id"])
    started = dt.datetime.fromisoformat(snap["debate"]["created_at"])
    ended = dt.datetime.fromisoformat(verdict["created_at"])
    seats = [f"{s.get('model')}" for s in snap.get("seats") or []]
    coder = [m for m in snap["messages"] if m.get("research_kind") in ("code", "codebrief") and m["status"] == "done"]
    return {
        "answer": answer["content"],
        "seconds": round((ended - started).total_seconds(), 1),
        "cost_usd": 0.0,
        "model": f"Quorum ({len(seats)} local models: {', '.join(sorted(set(seats)))})",
        "debate_id": debate_id,
        "coder_calls": len(coder),
    }


def run_quorum(case: Dict[str, Any], repo: Path, mode: str) -> Dict[str, Any]:
    mcp_server.ensure_backend()
    debate_id = mcp_server.start(request(case, repo), mode, False, pack="code-review", repo_path=str(repo))
    print(f"    quorum: {cli.APP_URL}/#q/{debate_id}", flush=True)
    while True:
        _, done = mcp_server.wait(debate_id, mcp_server.MAX_WAIT)
        if done:
            return quorum_answer(debate_id)


# ------------------------------------------------------------------ findings and scoring

_ITEM = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+(.*)")
_NONE = re.compile(r"\s*(none|no (issues|findings|problems|bugs)|nothing)\b", re.I)
_BOLD_TITLE = re.compile(r"^\*\*(.{1,160}?)\*\*:?\s*$")  # a list item that is only a bold title
_BOLD_LEAD = re.compile(r"^\s*\*\*(.{1,160}?)\*\*[:.]?\s*(\S.*)$")  # "**Title**: rest of the line"
_LABEL = re.compile(
    r"\s*(fix(es)?|suggested fix|why|impact|example|note|evidence|risk|details?|how|issue|problem|file|files|location|lines?)\b",
    re.I,
)
_ROW = re.compile(r"^\s*\|(.*)\|\s*$")
_HEADING = re.compile(r"^\s*(?:#{1,6}\s+(.*)|\*\*(.{1,160}?)\*\*:?\s*|([A-Z][\w &/()-]{0,60}):\s*)$")
SEVERITY = [
    ("high", re.compile(r"\bhigh\b|must.fix|critical|blocker|severe", re.I)),
    ("medium", re.compile(r"\bmedium\b|should.fix|moderate", re.I)),
    ("low", re.compile(r"\blow\b|nice.to.have|nit|minor|optional", re.I)),
]
NOT_FINDINGS = re.compile(r"correct|looks good|fine|strength|well done|positive|what.s right|differ|next step", re.I)
SUMMARY = re.compile(r"bottom line|key point|summary|overview|verdict|recommendation", re.I)


def severity_of(text: str) -> Optional[str]:
    for name, pattern in SEVERITY:
        if pattern.search(text):
            return name
    return None


def split_findings(answer: str) -> List[Dict[str, str]]:
    """The answer's findings, from the layouts reviewers use: list items or table rows under a High, Medium or Low
    heading (or starting with one of those words), or a numbered or bold title per finding followed by paragraphs and
    sub-bullets.
    Items under 'looks correct', 'where they differed', 'next steps' and similar headings aren't findings; items under
    'key points' or 'summary' count toward finding a bug but are never counted as false alarms."""
    items: List[Dict[str, Any]] = []
    section: Optional[str] = None  # severity of the current section; "skip", "summary", or None if unknown
    current: Optional[Dict[str, Any]] = None
    header: Optional[List[str]] = None  # the current table's header row
    in_code = False
    for line in answer.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        if in_code or line.strip().startswith(
            "```"
        ):  # code belongs to the finding it's in; a "# comment" isn't a heading
            if current is not None:
                current["text"] += " " + line.strip()
            continue
        if not line.strip():
            header = None
            if current is not None and not current["titled"]:
                current = None
            continue
        heading = _HEADING.match(line)
        item = _ITEM.match(line)
        if not item and _NONE.match(re.sub(r"[*_`]", "", line)):
            section, current = "skip", None  # "None." under a severity: what follows are concerns it ruled out
            continue
        lead = None if heading or item else _BOLD_LEAD.match(line)
        if lead and current is not None and _LABEL.match(lead.group(1)):  # "**Fix:** ..." belongs to the finding
            current["text"] += " " + line.strip()
            continue
        if lead:  # a finding titled in bold, with its file or detail on the same line
            title = re.sub(r"[*_`]", "", f"{lead.group(1)}: {lead.group(2)}").strip()
            sev = section if section in ("high", "medium", "low") else severity_of(lead.group(1)) or section
            current = {"severity": sev, "text": title, "titled": True}
            items.append(current)
            continue
        if heading and not item:
            title = re.sub(r"[*_`]", "", next(g for g in heading.groups() if g is not None)).strip()
            sev = severity_of(title)
            numbered = re.match(r"\d+[.)]\s", title)
            short = len(title) <= 45 or line.lstrip().startswith("#")  # a markdown heading can be a long section name
            if not numbered and short and (sev or NOT_FINDINGS.search(title) or SUMMARY.search(title)):
                section = sev or ("skip" if NOT_FINDINGS.search(title) else "summary")
                current = None
            elif section in ("high", "medium", "low") or numbered or sev:
                current = {"severity": section if section in ("high", "medium", "low") else sev, "text": title}
                current["titled"] = True
                items.append(current)
            else:
                section, current = None, None
            continue
        row = _ROW.match(line)
        if row:
            cells = [c.strip() for c in row.group(1).split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue  # the separator under a header
            if header is None:
                header = cells  # the first row of a table is its header
                continue
            text = " · ".join(c for c in cells if c)
            lead = next((severity_of(c) for c in cells if len(c) < 12 and severity_of(c)), None)
            current = {"severity": lead or section, "text": text, "titled": False}
            items.append(current)
            continue
        header = None
        if item:
            indented = len(line) - len(line.lstrip()) >= 2
            bold = _BOLD_TITLE.match(item.group(1))
            if bold and not indented and section not in ("high", "medium", "low", "summary"):
                # "1. **[High] Title (file:line):**" titles a finding whose sub-bullets follow;
                # "1. **Code fixes:**" without a severity only groups the findings under it
                title = re.sub(r"[*_`]", "", bold.group(1)).strip()
                sev = severity_of(title[:40])
                current = {"severity": sev, "text": title, "titled": True, "tagged": True} if sev else None
                if current:
                    items.append(current)
                continue
            if current is not None and (current["titled"] or indented):
                current["text"] += " " + item.group(1)
                continue
            text = item.group(1)
            lead = (
                severity_of(re.sub(r"[*_`]", "", text)[:40])
                if re.match(r"[*_]*(high|medium|low)\b", text, re.I)
                else None
            )
            current = {"severity": lead or section, "text": text, "titled": False}
            items.append(current)
        elif current is not None:
            current["text"] += " " + line.strip()
    findings = [i for i in items if i["severity"] in ("high", "medium", "low", "summary")]
    if not any(f["severity"] != "summary" and not f.get("tagged") for f in findings):
        # no severity structure (a few tagged titles aside): every item outside a skipped or summary section is medium
        findings += [{"severity": "medium", "text": i["text"]} for i in items if i["severity"] is None]
    return [{"severity": f["severity"], "text": f["text"]} for f in findings]


def matches(point: Dict[str, Any], text: str) -> bool:
    return all(re.search(p, text, re.I) for p in point["match"])


def score(case: Dict[str, Any], findings: List[Dict[str, str]]) -> Dict[str, Any]:
    found: Dict[str, str] = {}
    unmatched: List[Dict[str, str]] = []
    for f in findings:
        hits = [b["id"] for b in case["bugs"] if matches(b, f["text"])]
        for bug in hits:
            found.setdefault(bug, f["text"])
        if hits or f["severity"] in ("low", "summary"):
            continue
        # a fair point excuses a Medium finding, but on a clean change a High one claims a must-fix bug that isn't there
        high_on_clean = case.get("clean") and f["severity"] == "high"
        if high_on_clean or not any(matches(a, f["text"]) for a in case.get("acceptable", [])):
            unmatched.append(f)
    total = len(case["bugs"])
    return {
        "found": found,
        "missed": [b["id"] for b in case["bugs"] if b["id"] not in found],
        "recall": round(len(found) / total, 2) if total else None,
        "findings": len([f for f in findings if f["severity"] != "summary"]),
        "false_alarms": unmatched if case.get("clean") else [],
        "unmatched": [] if case.get("clean") else unmatched,
    }


# ------------------------------------------------------------------ report


def summarize(results: Dict[str, Any], cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Totals per reviewer over the cases it ran."""
    out = []
    for r in REVIEWERS:
        ran = [c for c in cases if r in results["cases"].get(c["name"], {})]
        if not ran:
            continue
        runs = [results["cases"][c["name"]][r] for c in ran]
        ok = [x for x in runs if "error" not in x]
        out.append(
            {
                "reviewer": r,
                "model": next((x["model"] for x in ok), ""),
                "planted": sum(len(c["bugs"]) for c in ran),
                "found": sum(len(x["score"]["found"]) for x in ok),
                "alarms": sum(len(x["score"]["false_alarms"]) for x in ok),
                "clean_cases": sum(1 for c in ran if c.get("clean")),
                "unmatched": sum(len(x["score"]["unmatched"]) for x in ok),
                "seconds": sum(x["seconds"] for x in ok),
                "per_review": sum(x["seconds"] for x in ok) / max(1, len(ok)),
                "cost": sum(x.get("cost_usd", 0) for x in ok),
                "reviews": len(ok),
                "failed": len(runs) - len(ok),
            }
        )
    return out


CHART_LABELS = {
    "lint": ("ruff", "linter, bug + security rules"),
    "local": ("One local model", "the council's largest, alone"),
    "claude": ("Claude Code", "frontier cloud model, alone"),
    "quorum": ("Quorum", "a council of local models + the Coder"),
}


def _minutes(seconds: float) -> str:
    if seconds < 1:
        return "under 1 s"
    return f"{seconds:.0f} s" if seconds < 90 else f"{seconds / 60:.0f} min"


def chart_svg(summary: List[Dict[str, Any]], dark: bool) -> str:
    """Planted bugs found per reviewer as horizontal bars, with false alarms, time and cost beside each."""
    fg, sub, track, grey, accent = (
        ("#f5f5f7", "#98989d", "#2c2c2e", "#8e8e93", "#0a84ff")
        if dark
        else ("#1d1d1f", "#6e6e73", "#ececf0", "#aeaeb2", "#0071e3")
    )
    w, left, bar_w, row = 780, 220, 290, 56
    planted = max((s["planted"] for s in summary), default=0)
    h = 70 + row * len(summary) + 18
    font = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" font-family="{font}">',
        f'<text x="0" y="22" font-size="17" font-weight="600" fill="{fg}">Planted bugs found</text>',
        f'<text x="0" y="44" font-size="13" fill="{sub}">{planted} bugs planted in real-looking changes; '
        "false alarms counted on clean changes</text>",
    ]
    for i, s in enumerate(summary):
        y = 70 + i * row
        name, note = CHART_LABELS.get(s["reviewer"], (s["reviewer"], ""))
        council = re.search(r"(\d+) local models", s["model"] or "")
        if s["reviewer"] == "quorum" and council:
            note = f"{council.group(1)} local models + the Coder"
        share = s["found"] / s["planted"] if s["planted"] else 0
        color = accent if s["reviewer"] == "quorum" else grey
        cost = "$0 API" if not s["cost"] else f"${s['cost'] / max(1, s['reviews']):.2f}"
        if not s["clean_cases"]:
            alarms = "false alarms: not measured yet"
        elif not s["alarms"]:
            alarms = "no false alarms"
        else:
            alarms = f"{s['alarms']} false alarm{'s' * (s['alarms'] != 1)}"
        weight = "700" if s["reviewer"] == "quorum" else "600"
        parts += [
            f'<text x="0" y="{y + 17}" font-size="14" font-weight="{weight}" fill="{fg}">{name}</text>',
            f'<text x="0" y="{y + 35}" font-size="11.5" fill="{sub}">{note}</text>',
            f'<rect x="{left}" y="{y + 6}" width="{bar_w}" height="22" rx="6" fill="{track}"/>',
            f'<rect x="{left}" y="{y + 6}" width="{max(4, bar_w * share):.1f}" height="22" rx="6" fill="{color}"/>',
            f'<text x="{left + bar_w + 12}" y="{y + 22}" font-size="14" font-weight="{weight}" fill="{fg}">'
            f"{share:.0%}</text>",
            f'<text x="{left + bar_w + 66}" y="{y + 16}" font-size="12" fill="{sub}">{alarms}</text>',
            f'<text x="{left + bar_w + 66}" y="{y + 32}" font-size="12" fill="{sub}">'
            f"{_minutes(s['per_review'])} per review · {cost}</text>",
        ]
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def report(results: Dict[str, Any], cases: List[Dict[str, Any]]) -> str:
    reviewers = [r for r in REVIEWERS if any(r in results["cases"].get(c["name"], {}) for c in cases)]
    lines = [
        "# Code-review benchmark",
        "",
        f"Run {results['date']}. Cases: `benchmarks/review/cases/`. Method: `scripts/benchmark_review.py`.",
        "",
        "## Summary",
        "",
        "| Reviewer | Planted bugs found | False alarms on clean changes | Unmatched findings (to judge) | Time | Cost |",
        "|---|---|---|---|---|---|",
    ]
    for s in summarize(results, cases):
        errors = f" ({s['failed']} failed)" if s["failed"] else ""
        lines.append(
            f"| {s['reviewer']}{errors} | {s['found']} of {s['planted']} ({s['found'] / s['planted']:.0%}) | "
            f"{s['alarms']} | {s['unmatched']} | {s['seconds'] / 60:.1f} min | ${s['cost']:.2f} |"
        )
    lines += ["", "Cost is API spend; local models cost $0 beyond electricity. Time is wall-clock for all cases.", ""]
    for c in cases:
        runs = results["cases"].get(c["name"], {})
        kind = "clean change (no bugs)" if c.get("clean") else f"{len(c['bugs'])} planted bugs"
        lines += [f"## {c['name']}: {kind}", "", f"Task: {c['task']}", ""]
        if c["bugs"]:
            lines += ["| Bug | " + " | ".join(r for r in reviewers if r in runs) + " |"]
            lines += ["|---|" + "---|" * len([r for r in reviewers if r in runs])]
            for b in c["bugs"]:
                cells = []
                for r in reviewers:
                    if r not in runs:
                        continue
                    x = runs[r]
                    cells.append("error" if "error" in x else ("✓" if b["id"] in x["score"]["found"] else "·"))
                lines.append(f"| {b['what']} | " + " | ".join(cells) + " |")
            lines.append("")
        for r in reviewers:
            x = runs.get(r)
            if not x:
                continue
            if "error" in x:
                lines += [f"**{r}** failed: {x['error']}", ""]
                continue
            s = x["score"]
            n = len([f for f in x["findings"] if f["severity"] != "summary"])
            extra = (
                f", conundrum `{x['debate_id']}`, Coder calls: {x.get('coder_calls', 0)}" if x.get("debate_id") else ""
            )
            lines.append(f"**{r}** ({x['model']}; {x['seconds'] / 60:.1f} min{extra}): {n} findings")
            for f in s["false_alarms"]:
                lines.append(f"- false alarm ({f['severity']}): {f['text'][:220]}")
            for f in s["unmatched"]:
                lines.append(f"- unmatched ({f['severity']}): {f['text'][:220]}")
            lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------ main


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--reviewers", default=",".join(REVIEWERS))
    ap.add_argument("--cases", default="", help="comma-separated case names (default: all)")
    ap.add_argument("--local-model", default="", help="Ollama model for the single-model reviewer (default: largest)")
    ap.add_argument("--mode", default="standard", choices=["quick", "standard", "deep"])
    ap.add_argument("--quorum-id", action="append", default=[], help="case=conundrum_id: score a finished review")
    ap.add_argument("--rescore", help="results.json to score again (after changing case patterns)")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    cases = load_cases([c for c in args.cases.split(",") if c] or None)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.rescore:
        results = json.loads(Path(args.rescore).read_text())
        for c in cases:
            for x in results["cases"].get(c["name"], {}).values():
                if "error" not in x:
                    if "answer" in x:
                        x["findings"] = split_findings(x["answer"])
                    x["score"] = score(c, x["findings"])
    else:
        previous = out / "results.json"
        results = json.loads(previous.read_text()) if previous.exists() else {"cases": {}}
        results["date"] = dt.date.today().isoformat()
        reviewers = [r for r in args.reviewers.split(",") if r in REVIEWERS]
        reuse = dict(pair.split("=", 1) for pair in args.quorum_id)
        model = args.local_model or (largest_local_model() if "local" in reviewers else "")
        for c in cases:
            print(f"{c['name']}", flush=True)
            runs = results["cases"].setdefault(c["name"], {})
            with tempfile.TemporaryDirectory(prefix=f"quorum-bench-{c['name']}-") as tmp:
                repo = build_repo(c, Path(tmp).resolve())
                for r in reviewers:
                    print(f"  {r}…", flush=True)
                    try:
                        if r == "lint":
                            x = run_lint(c, repo)
                        elif r == "local":
                            x = run_local(c, repo, model)
                        elif r == "claude":
                            x = run_claude(c, repo)
                        elif c["name"] in reuse:
                            x = quorum_answer(reuse[c["name"]])
                        else:
                            x = run_quorum(c, repo, args.mode)
                        if "findings" not in x:
                            x["findings"] = split_findings(x["answer"])
                        x["score"] = score(c, x["findings"])
                        print(f"    {len(x['score']['found'])}/{len(c['bugs'])} found, {x['seconds']}s", flush=True)
                    except Exception as e:  # one reviewer failing shouldn't lose the others' results
                        x = {"error": str(e)[:300]}
                        print(f"    failed: {e}", flush=True)
                    runs[r] = x
                    (out / "results.json").write_text(json.dumps(results, indent=2))
    (out / "results.json").write_text(json.dumps(results, indent=2))
    reported = [c for c in load_cases() if c["name"] in results["cases"]]
    notes = out / "notes.md"  # by-hand judgments of unmatched findings, kept across reruns
    extra = f"\n{notes.read_text().strip()}\n" if notes.exists() else ""
    (out / "README.md").write_text(report(results, reported) + "\n" + extra)
    # the chart's story: a linter, one local model, the same local models together, then a frontier model for scale
    order = ["lint", "local", "quorum", "claude"]
    summary = sorted(summarize(results, reported), key=lambda s: order.index(s["reviewer"]))
    for dark in (False, True):
        (out / f"chart-{'dark' if dark else 'light'}.svg").write_text(chart_svg(summary, dark))
    print(f"wrote {out / 'README.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
