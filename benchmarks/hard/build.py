"""Builds benchmarks/hard/questions.json: harder questions with one checkable answer each. Every answer is computed
here, never typed by hand.

    python3 benchmarks/hard/build.py > benchmarks/hard/questions.json
"""

import contextlib
import datetime as dt
import io
import itertools
import json
import math
import sys
from fractions import Fraction

PY = "What exactly does this Python 3 program print?\n\n```python\n{code}\n```"

programs = {
    "py-fib-sign": """def g(n):
    if n <= 1:
        return n
    return g(n - 1) + g(n - 2) * (1 if n % 3 else -1)

print(g(12))""",
    "py-slice": """s = "abracadabra"
print(s[::-3] + s[1:-1:4])""",
    "py-nonlocal": """x = 10

def outer():
    x = 1
    def inner():
        nonlocal x
        x += 5
        return x
    return inner

f = outer()
f()
print(f() + x)""",
    "py-mississippi": """d = {}
for i, ch in enumerate("mississippi"):
    d[ch] = d.get(ch, 0) + i
best = max(d, key=d.get)
print(best, d[best])""",
    "py-finally": """def f():
    try:
        return 1
    finally:
        return 2

print(f() * 10 + len([f() for _ in range(3)]))""",
    "py-zip-iter": """it = iter(range(11))
pairs = list(zip(it, it))
print(len(pairs), next(it, -1))""",
    "py-grid": """grid = [[0] * 3] * 3
grid[0][0] = 1
grid[1][1] += 2
grid[2] = [5, 5, 5]
print(sum(map(sum, grid)))""",
    "py-for-else": """n = 0
for i in range(6):
    for j in range(i):
        if (i + j) % 4 == 0:
            break
        n += 1
    else:
        n += 10
print(n)""",
    "py-floordiv": """print(-7 // 2 + -7 % 3 + 7 // -2 + 7 % -3)""",
    "py-bools": """print(sum([True, True, 3]) * (True + True) ** 3)""",
    "py-class-attr": """class A:
    items = []
    def add(self, x):
        self.items.append(x)
        return len(self.items)

a, b = A(), A()
a.add(1)
b.add(2)
b.items = [9]
print(a.add(3) + len(b.items))""",
    "py-remove-loop": """x = [1, 3, 5, 6, 7, 9, 10]
for i in x:
    if i % 2:
        x.remove(i)
print(x)""",
    "py-lru-calls": """from functools import lru_cache

calls = 0

@lru_cache(maxsize=None)
def c(n):
    global calls
    calls += 1
    return 1 if n < 2 else c(n - 1) + c(n - 2)

c(30)
c(35)
print(calls)""",
    "py-split": """t = " a  b ,c "
print(len(t.split()), len(t.split(" ")), len(t.split(",")))""",
    "py-skip-threes": """s = 0
for k in range(1, 100):
    if k % 3 == 0 or "3" in str(k):
        continue
    s += k
print(s)""",
    "py-send": """def gen():
    total = 0
    while True:
        x = yield total
        if x is None:
            break
        total += x * 2 if total % 2 else x

g = gen()
next(g)
for v in [5, 7, 11, 4]:
    last = g.send(v)
print(last)""",
    "py-sort-key": """words = ["b10", "a2", "b9", "a10", "c1", "b1"]
print(sorted(words, key=lambda s: (s[0], -int(s[1:])))[2:5])""",
    "py-int-bases": """print(int("0b101", 0) + int("17", 8) + int("z", 36) + int("0x1F", 16))""",
    "py-bits": """print(bin(37 ^ 21).count("1") * (37 & 21) - (37 | 21))""",
    "py-default-dict": """from collections import defaultdict

d = defaultdict(list)
for w in "the quick brown fox jumps over the lazy dog".split():
    d[len(w)].append(w)
print(len(d), len(d[3]), d[5][-1])""",
    "py-str-mul": """s = "ab" * 3
print(s.count("aba") + s.find("ba", 2) + s.rfind("b", 0, 4))""",
    "py-dict-order": """d = {"a": 1, "b": 2, "c": 3}
del d["a"]
d["a"] = 4
d["b"] = 5
print("".join(d), sum(d.values()))""",
}


def run(code):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(code, {"__name__": "__main__"})
    return buf.getvalue().strip()


questions = []
for qid, code in programs.items():
    out = run(code)
    questions.append({"id": qid, "kind": "python", "question": PY.format(code=code), "answer": out, "accept": [out]})


def q(qid, text, answer, accept=None):
    answer = str(answer)
    questions.append({"id": qid, "kind": "reasoning", "question": text, "answer": answer, "accept": accept or [answer]})


# ---------------------------------------------------------------- counting and number theory

q("not-6-10-15", "How many integers from 1 to 1000 are divisible by none of 6, 10 and 15?",
  sum(1 for n in range(1, 1001) if n % 6 and n % 10 and n % 15))

# trailing zeros of 200! in base 12 = min(v2 // 2, v3)
v2 = sum(200 // 2**k for k in range(1, 9))
v3 = sum(200 // 3**k for k in range(1, 6))
q("zeros-base12", "Written in base 12, how many zeros does 200! (200 factorial) end with?", min(v2 // 2, v3))

def climbs(n, steps=(1, 2, 3)):
    ways = [1] + [0] * n
    for i in range(1, n + 1):
        ways[i] = sum(ways[i - s] for s in steps if i >= s)
    return ways[n]


q("stairs-123", "You climb a 15-step staircase taking 1, 2 or 3 steps at a time. In how many different orders can "
  "you reach the top?", climbs(15))

q("digitsum-2-100", "What is the sum of the decimal digits of 2 to the power 100?", sum(map(int, str(2**100))))

q("last-two-7", "What are the last two digits of 7 to the power 2026?", f"{pow(7, 2026, 100):02d}")

q("divisors-10f", "How many positive divisors does 10! (10 factorial) have?",
  sum(1 for d in range(1, math.factorial(10) + 1) if math.factorial(10) % d == 0))

q("digit-sum-20", "How many integers from 1 to 10,000 have digits that add up to exactly 20?",
  sum(1 for n in range(1, 10001) if sum(map(int, str(n))) == 20))

q("coins-50", "In how many ways can you pay exactly 50 cents using any number of 1-, 2- and 5-cent coins? (Only the "
  "number of each coin matters, not the order.)",
  sum(1 for b in range(26) for c in range(11) if 2 * b + 5 * c <= 50))

q("no-11", "How many strings of 12 bits (0s and 1s) contain no two 1s next to each other?",
  sum(1 for bits in itertools.product("01", repeat=12) if "11" not in "".join(bits)))

q("bananas", "How many distinct arrangements of the letters of the word BANANAS are there in which no two A's are "
  "next to each other?",
  len({p for p in itertools.permutations("BANANAS") if "AA" not in "".join(p)}))

q("palindromes-sum", "What is the sum of all four-digit palindromes (like 1221)?",
  sum(n for n in range(1000, 10000) if str(n) == str(n)[::-1]))

q("zeros-100", "What is the smallest positive integer n such that n! (n factorial) ends in exactly 100 zeros?",
  next(n for n in range(1, 1000) if sum(n // 5**k for k in range(1, 6)) == 100))

def josephus(n, k):
    people, i = list(range(1, n + 1)), 0
    while len(people) > 1:
        i = (i + k - 1) % len(people)
        people.pop(i)
    return people[0]


q("josephus", "41 people stand in a circle, numbered 1 to 41. Starting from person 1, every 3rd person still in the "
  "circle is removed (first 3, then 6, and so on, wrapping around). What is the number of the last person left?",
  josephus(41, 3))

# ---------------------------------------------------------------- probability

deck_both = Fraction(13 * 12, 52 * 51)
deck_one = 1 - Fraction(39 * 38, 52 * 51)
q("hearts-given", "Two cards are drawn from a standard 52-card deck without replacement. Given that at least one of "
  "them is a heart, what is the probability that both are hearts? Give a fraction in lowest terms.",
  deck_both / deck_one)

q("coupon-die", "You roll a fair six-sided die until every face has appeared at least once. What is the expected "
  "number of rolls? Give the exact value as a fraction or a decimal to 2 places.",
  sum(Fraction(6, k) for k in range(1, 7)), [str(sum(Fraction(6, k) for k in range(1, 7))), "14.7"])

q("four-doors", "A game show has 4 doors: a car behind one, goats behind three. You pick a door. The host, who knows "
  "where the car is, opens 2 of the other doors, both with goats. You switch to the one remaining closed door. What "
  "is the probability you win the car? Give a fraction.", Fraction(3, 4))

q("tuesday-boy", "A family has two children. You learn that at least one of them is a boy born on a Tuesday. "
  "Assuming each child is equally likely to be a boy or a girl and equally likely to be born on any day of the week, "
  "independently, what is the probability that both children are boys? Give a fraction.", Fraction(13, 27))

q("birthday-30", "In a room of 30 people with birthdays spread evenly over 365 days (ignore leap years), what is the "
  "probability that at least two share a birthday? Give it to 3 decimal places.",
  f"{1 - math.prod((365 - i) / 365 for i in range(30)):.3f}")

three_dice = Fraction(sum(1 for a, b, c in itertools.product(range(1, 7), repeat=3) if a + b + c == 10), 216)
q("three-dice-10", "Three fair six-sided dice are rolled. What is the probability that they sum to exactly 10? Give a "
  "fraction in lowest terms.", three_dice)

# ---------------------------------------------------------------- dates, rates and traps

q("march-2100", "What day of the week will 1 March 2100 be?", dt.date(2100, 3, 1).strftime("%A"))

fri13 = sum(1 for y in range(2001, 2031) for m in range(1, 13) if dt.date(y, m, 13).weekday() == 4)
q("friday-13", "How many Friday the 13ths are there from 1 January 2001 to 31 December 2030, inclusive?", fri13)

q("days-between", "How many days are there from 10 February 2024 to 1 March 2025, counting both of those dates?",
  (dt.date(2025, 3, 1) - dt.date(2024, 2, 10)).days + 1)

q("clock-738", "What is the smaller angle, in degrees, between the hour and minute hands of a clock at 7:38?",
  abs(30 * 7 + 0.5 * 38 - 6 * 38) if abs(30 * 7 + 0.5 * 38 - 6 * 38) <= 180 else 360 - abs(30 * 7 + 0.5 * 38 - 6 * 38))

# A fills in 6 h, B in 9 h, drain C empties in 12 h; A and B from time 0, C opened at 1 h
rate_ab = Fraction(1, 6) + Fraction(1, 9)
t = 1 + (1 - rate_ab) / (rate_ab - Fraction(1, 12))
q("pipes", "An empty tank has two inlet pipes and a drain. Pipe A alone fills it in 6 hours, pipe B alone in 9 hours, "
  "and the drain alone empties a full tank in 12 hours. A and B are opened at the same time; the drain is opened "
  "1 hour later. How many hours after A and B were opened is the tank full? Give an exact fraction or a decimal to "
  "2 places.", t, [str(t), f"{float(t):.2f}"])

q("avg-speed", "A car drives 60 km at 30 km/h, then 60 km at 90 km/h, then 30 km at 60 km/h. What is its average "
  "speed over the whole trip, in km/h? Give an exact fraction or a decimal to 2 places.",
  Fraction(150) / (Fraction(60, 30) + Fraction(60, 90) + Fraction(30, 60)),
  [str(Fraction(150) / (Fraction(60, 30) + Fraction(60, 90) + Fraction(30, 60))),
   f"{150 / (2 + 60 / 90 + 0.5):.2f}"])

q("slow-clock", "A clock loses 3 minutes every hour. It is set to the right time at 12:00 noon. When it shows 6:00 pm "
  "the same day, how many real minutes have passed since noon? Round to the nearest minute.", round(360 * 60 / 57))

def fewest_for_pairs(stock, want):
    """The fewest socks that always contain `want` pairs, whatever colours come out."""
    for n in range(1, sum(stock) + 1):
        draws = (c for c in itertools.product(*(range(s + 1) for s in stock)) if sum(c) == n)
        if min(sum(x // 2 for x in c) for c in draws) >= want:
            return n


q("socks-colors", "A drawer holds 10 red, 8 blue and 6 green socks, unsorted. In the dark, what is the fewest socks "
  "you must take to be sure of having 2 matching pairs (two pairs, which may be the same colour)?",
  fewest_for_pairs([10, 8, 6], 2))

q("ages-ratio", "A mother is 4 times as old as her son. In 20 years she will be twice as old as him. How old is the "
  "mother now?", next(4 * s for s in range(1, 50) if 4 * s + 20 == 2 * (s + 20)))


# Whole numbers computed as floats print as 1.0
for item in questions:
    a = item["answer"]
    if a.endswith(".0"):
        item["answer"] = a[:-2]
        item["accept"] = [a[:-2]]

FORMAT = "\n\nStart your reply with one line of the form ANSWER: <the exact value or output>, then explain briefly."
# strict: only the ANSWER line is graded, for the model alone and for Quorum alike
json.dump({"format": FORMAT, "strict": True, "questions": questions}, sys.stdout, indent=1, ensure_ascii=False)
print()
