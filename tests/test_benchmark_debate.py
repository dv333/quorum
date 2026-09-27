"""The debate benchmark's grading: an answer counts when it states the computed value, in any usual form."""

import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, "scripts")
spec = importlib.util.spec_from_file_location("benchmark_debate", "scripts/benchmark_debate.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)

QUESTIONS = {q["id"]: q for q in json.loads(Path("benchmarks/debate/questions.json").read_text())["questions"]}


def test_a_probability_counts_as_a_fraction_decimal_or_percentage():
    q = QUESTIONS["monty"]
    for text in ["2/3 — switching wins twice as often", "About 0.667.", "66.7% if you switch", "Switch: 66.67%"]:
        assert bench.graded(q, text), text
    assert not bench.graded(q, "1/2: after a door opens it's a coin flip")


def test_the_first_number_is_the_answer():
    q = QUESTIONS["bat-ball"]
    assert bench.graded(q, "$0.05. Not 10 cents: then the bat would cost $1.10")
    assert not bench.graded(q, "$0.10. The bat is $1.00")


def test_program_output_and_words_match_as_written():
    assert bench.graded(QUESTIONS["py-11"], "It prints `1 c`: 1, 1.0 and True are the same key")
    assert bench.graded(QUESTIONS["y2k-day"], "It was a Saturday.")
    assert not bench.graded(QUESTIONS["y2k-day"], "Sunday")
    assert bench.graded(QUESTIONS["py-07"], "-4, because // floors toward minus infinity")
    assert not bench.graded(QUESTIONS["py-07"], "-3 (it truncates)")


def test_a_percentage_change_can_be_said_in_words():
    q = QUESTIONS["up-down"]
    assert bench.graded(q, "A 1% decrease overall") and bench.graded(q, "-1%")
    assert not bench.graded(q, "No change: +10% and -10% cancel out")


def test_the_bottom_line_is_read_from_the_answer():
    assert bench.bottom_line("BOTTOM LINE: **It prints `6`.**\n\n## Key points\n- x") == "It prints 6."
    assert bench.bottom_line("47.\nBecause it doubles.") == "47."


def test_every_question_has_its_own_answer_among_the_accepted_forms():
    for q in QUESTIONS.values():
        assert bench.graded(q, q["answer"]), q["id"]


def test_latex_fractions_count():
    q = QUESTIONS["monty"]
    assert bench.graded(q, r"The probability of winning by switching is \( \frac{2}{3} \).")
    assert bench.graded(q, r"$\dfrac{2}{3}$")
    assert not bench.graded(q, r"\( \frac{1}{2} \)")


def test_a_direct_answer_counts_for_the_final_answer_but_not_for_disagreement():
    qs = [QUESTIONS["monty"], QUESTIONS["bat-ball"]]
    agents = [{"model": "m", "correct": True}, {"model": "m", "correct": False}]
    quorum = {"status": "concluded", "seconds": 60, "draft_round1_correct": True}
    results = {
        "monty": {"quorum": {**quorum, "agents": agents, "final_correct": True}},
        "bat-ball": {"quorum": {**quorum, "agents": [], "draft_round1_correct": None, "final_correct": True}},
    }
    s = bench.summarize(results, qs)
    assert (s["direct"], s["split_questions"], s["final_correct"]) == (1, 1, 2)
