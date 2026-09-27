"""The release quality check fails on a real drop, not on noise."""

import importlib.util

spec = importlib.util.spec_from_file_location("quality_check", "scripts/quality_check.py")
qc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qc)

BASE = {"date": "2026-09-27", "debate_right": 4, "review_found": 17}


def test_one_less_is_noise():
    assert qc.compare({"debate_right": 3, "review_found": 16}, BASE) == []


def test_two_less_on_either_part_fails():
    worse = qc.compare({"debate_right": 2, "review_found": 17}, BASE)
    assert len(worse) == 1 and "debate questions right: 2, was 4" in worse[0]
    assert qc.compare({"debate_right": 4, "review_found": 15}, BASE)


def test_a_review_that_errored_fails():
    assert qc.compare({"debate_right": 4, "review_found": 17, "review_errors": ["orders"]}, BASE)


def test_the_checked_questions_and_cases_exist():
    import json
    from pathlib import Path

    ids = {q["id"] for q in json.loads(Path("benchmarks/debate/questions.json").read_text())["questions"]}
    assert set(qc.DEBATE_IDS) <= ids
    assert all((Path("benchmarks/review/cases") / c).exists() for c in qc.REVIEW_CASES)
