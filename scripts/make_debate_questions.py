"""Writes benchmarks/debate/questions.json: questions with one checkable answer each, for the debate benchmark.

Every answer is computed here, not typed in: Python snippets are run, and the math and probability answers come from
the calculation (or an exact count). Each question lists the spellings of its answer that count as correct.

    uv run python scripts/make_debate_questions.py
"""

import contextlib
import datetime as dt
import io
import itertools
import json
import math
from fractions import Fraction
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "benchmarks" / "debate" / "questions.json"

# What does this print? Small programs where Python's rules trip people (and models) up
SNIPPETS = [
    "def f(x, items=[]):\n    items.append(x)\n    return items\n\nf(1)\nprint(len(f(2)))",
    "print(round(2.5) + round(3.5))",
    "print(0.1 + 0.2 == 0.3)",
    "fs = [lambda: i for i in range(3)]\nprint(sum(f() for f in fs))",
    "a = [1, 2, 3]\nb = a\nb += [4]\nprint(len(a))",
    "t = (1, 2)\nu = t\nu += (3,)\nprint(len(t))",
    "print(-7 // 2)",
    "print(-7 % 3)",
    "print(int(-3.7))",
    "g = (x * x for x in range(4))\nprint(sum(g) + sum(g))",
    "d = {}\nd[1] = 'a'\nd[1.0] = 'b'\nd[True] = 'c'\nprint(len(d), d[1])",
    "x = [[0] * 2] * 2\nx[0][0] = 5\nprint(x[1][0])",
    "s = 'hello'\nprint(s[::-2])",
    "print(bool('False') + bool(0.0))",
    "print(len({1, 2, 2, 3} | {3, 4}))",
    "def g():\n    try:\n        return 1\n    finally:\n        return 2\n\nprint(g())",
    "print(sum(range(1, 10, 3)))",
    "print('%d%%' % 7.9)",
    "x = 5\n\ndef h():\n    return x\n\nx = 6\nprint(h())",
    "print(max('apple', 'Banana'))",
    "print(2 ** 3 ** 2)",
    "print([1, 2, 3][-4:2])",
]


def run(code: str) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(code, {})
    return out.getvalue().strip()


def num(x) -> list:
    """Spellings of a number that count as correct: 0.05, $0.05, 5 cents; 2/3, 0.667, 66.7%."""
    if isinstance(x, Fraction):
        forms = {f"{x.numerator}/{x.denominator}", f"{float(x):.3f}".rstrip("0"), f"{float(x) * 100:.1f}%"}
        if round(float(x) * 100, 1) == round(float(x) * 100):
            forms.add(f"{round(float(x) * 100)}%")
        return sorted(forms)
    if isinstance(x, float) and not x.is_integer():
        return [f"{x:.2f}".rstrip("0").rstrip(".")]
    return [str(int(x))]


def weekday(y, m, d) -> str:
    return dt.date(y, m, d).strftime("%A")


def monty_hall() -> Fraction:
    wins = total = 0
    for car, pick in itertools.product(range(3), range(3)):
        opened = next(d for d in range(3) if d not in (car, pick))
        switched = next(d for d in range(3) if d not in (pick, opened))
        wins += switched == car
        total += 1
    return Fraction(wins, total)


def two_children() -> Fraction:
    families = [kids for kids in itertools.product("BG", repeat=2) if "B" in kids]
    return Fraction(sum(kids == ("B", "B") for kids in families), len(families))


def birthday() -> int:
    n, p = 1, 1.0
    while True:
        p *= (365 - n) / 365
        n += 1
        if 1 - p > 0.5:
            return n


def squares_on_board() -> int:
    return sum((9 - k) ** 2 for k in range(1, 9))


def sevens() -> int:
    return sum(str(i).count("7") for i in range(1, 101))


def build() -> list:
    qs = []
    for i, code in enumerate(SNIPPETS, 1):
        out = run(code)
        qs.append({
            "id": f"py-{i:02d}", "kind": "python",
            "question": f"What exactly does this Python 3 program print?\n\n```python\n{code}\n```",
            "answer": out, "accept": [out],
        })  # fmt: skip
    math_qs = [
        (
            "bat-ball",
            "A bat and a ball cost $1.10 in total. The bat costs $1.00 more than the ball. How much does the "
            "ball cost, in dollars?",
            0.05,
            ["0.05", "5 cents"],
        ),
        (
            "lily-pads",
            "A patch of lily pads doubles in size every day. It covers the whole lake on day 48. On which day "
            "does it cover half the lake?",
            47,
            None,
        ),
        (
            "widgets",
            "5 machines make 5 widgets in 5 minutes. How many minutes do 100 machines take to make 100 widgets?",
            5,
            None,
        ),
        (
            "compound",
            "$1,000 earns 5% interest compounded yearly. How many dollars is it worth after 3 years, to the cent?",
            round(1000 * 1.05**3, 2),
            None,
        ),
        (
            "round-trip",
            "You drive to a town at 60 km/h and back the same way at 40 km/h. What is your average speed for "
            "the whole trip, in km/h?",
            2 * 60 * 40 / (60 + 40),
            None,
        ),
        (
            "zeros",
            "How many trailing zeros does 100! (100 factorial) have?",
            len(str(math.factorial(100))) - len(str(math.factorial(100)).rstrip("0")),
            None,
        ),
        ("y2k-day", "What day of the week was 1 January 2000?", weekday(2000, 1, 1), None),
        (
            "multiples",
            "What is the sum of all integers from 1 to 100 that are divisible by 3 or by 5?",
            sum(i for i in range(1, 101) if i % 3 == 0 or i % 5 == 0),
            None,
        ),
        (
            "discount",
            "A shirt costs $80 after a 20% discount. What was its original price, in dollars?",
            80 / 0.8,
            None,
        ),
        (
            "up-down",
            "A price rises 10% and then falls 10%. What is the overall change, in percent?",
            -1,
            ["-1%", "−1%", "1% decrease", "1% lower", "down 1%", "decrease of 1%", "decreased by 1%", "-1"],
        ),
        (
            "handshakes",
            "10 people each shake hands once with everyone else. How many handshakes are there?",
            math.comb(10, 2),
            None,
        ),
        ("chessboard", "How many squares of any size are there on an 8 by 8 chessboard?", squares_on_board(), None),
        (
            "clock",
            "What is the smaller angle between the hands of a clock at 3:15, in degrees?",
            abs(30 * 3 + 0.5 * 15 - 6 * 15),
            None,
        ),
        ("digits", "How many digits does 2 to the power 100 have?", len(str(2**100)), None),
        (
            "dice-seven",
            "Two fair dice are rolled. What is the probability that they sum to 7?",
            Fraction(sum(a + b == 7 for a in range(1, 7) for b in range(1, 7)), 36),
            None,
        ),
        (
            "monty",
            "In the Monty Hall game (3 doors, the host always opens a goat door you didn't pick and offers a "
            "switch), what is the probability of winning if you switch?",
            monty_hall(),
            None,
        ),
        (
            "two-children",
            "A family has two children and at least one of them is a boy. What is the probability that both are boys?",
            two_children(),
            None,
        ),
        (
            "birthday",
            "What is the smallest number of people for which the chance that two share a birthday is over "
            "50% (365 equally likely birthdays)?",
            birthday(),
            None,
        ),
        (
            "three-flips",
            "A fair coin is flipped 3 times. What is the probability of at least one head?",
            1 - Fraction(1, 8),
            None,
        ),
        (
            "socks",
            "A drawer has 10 black and 10 white socks. In the dark, how many socks must you take to be sure of a "
            "matching pair?",
            3,
            None,
        ),
        (
            "log",
            "It takes 6 minutes to cut a log into 3 pieces. At the same rate, how many minutes to cut one into 6 "
            "pieces?",
            6 / 2 * 5,
            None,
        ),
        (
            "ages",
            "A father is 4 times as old as his son. In 20 years he will be twice as old as his son. How old is the "
            "son now?",
            next(s for s in range(1, 100) if 4 * s + 20 == 2 * (s + 20)),
            None,
        ),
        ("sevens", "How many times is the digit 7 written when you write the numbers from 1 to 100?", sevens(), None),
        ("die-six", "On average, how many rolls of a fair die does it take to get the first 6?", 6, None),
        (
            "bird",
            "Two trains 300 km apart head toward each other at 70 km/h and 80 km/h. A bird flies back and forth "
            "between them at 100 km/h until they meet. How many km does the bird fly?",
            300 / (70 + 80) * 100,
            None,
        ),
        (
            "leap-days",
            "How many days are there from 1 February 2024 to 1 March 2024, not counting the first day?",
            (dt.date(2024, 3, 1) - dt.date(2024, 2, 1)).days,
            None,
        ),
        ("percent-of", "What is 40% of 25% of 800?", 0.4 * 0.25 * 800, None),
        (
            "ages-sum",
            "Anna is twice as old as Ben was when Anna was as old as Ben is now. Anna is 24. How old is Ben?",
            next(b for b in range(1, 24) if 24 == 2 * (b - (24 - b))),
            None,
        ),
    ]
    for qid, text, value, accept in math_qs:
        answer = (
            value
            if isinstance(value, str)
            else num(value if not isinstance(value, float) or not value.is_integer() else int(value))
        )
        forms = [answer] if isinstance(answer, str) else answer
        qs.append({
            "id": qid, "kind": "reasoning", "question": text,
            "answer": forms[0], "accept": sorted(set((accept or []) + forms)),
        })  # fmt: skip
    return qs


def main() -> None:
    qs = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"questions": qs}, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {len(qs)} questions to {OUT}")


if __name__ == "__main__":
    main()
