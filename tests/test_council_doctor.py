import json

import pytest

from backend import cli, db, diagnostics, inventory, packs
from backend.config import AUTO_COUNCIL_MIN
from tests.test_engine import FakeClient, make_debate, reply

GB = 1024**3


@pytest.fixture(autouse=True)
def memory_db():
    db.connect(":memory:")
    yield


def model(name, gb, fit="fits", chat=True, local=True):
    return {"model": name, "est_bytes": gb * GB, "fit": fit, "chat": chat, "local": local}


INSTALLED = [
    model("qwen3:30b", 20),
    model("gpt-oss:20b", 14),
    model("qwen2.5-coder:14b", 10),
    model("gemma3:12b", 9),
    model("devstral:24b", 16),
    model("llama3.1:8b", 6),
    model("nomic-embed-text", 1, chat=False),
    model("gpt-oss:120b", 70, fit="too_big"),
]
CODE = {"prefer": ["coder", "devstral"], "suggest": ["qwen3-coder:30b", "qwen2.5-coder:7b"], "seats": 4}


def names(seats):
    return [s["model"] for s in seats]


# ------------------------------------------------------------------ a council built for the pack


def test_no_pack_means_every_runnable_model_joins():
    seats, specialists = inventory.pick_council_for_pack(INSTALLED, 64 * GB, None)
    assert names(seats) == [
        "qwen3:30b",
        "devstral:24b",
        "gpt-oss:20b",
        "qwen2.5-coder:14b",
        "gemma3:12b",
        "llama3.1:8b",
    ]
    assert specialists == []


def test_pack_puts_specialists_with_the_strongest_generalists_largest_first():
    seats, specialists = inventory.pick_council_for_pack(INSTALLED, 64 * GB, CODE)
    assert specialists == ["devstral:24b", "qwen2.5-coder:14b"]
    # two specialists plus the two largest generalists; the largest still comes first (it picks the chair)
    assert names(seats) == ["qwen3:30b", "devstral:24b", "gpt-oss:20b", "qwen2.5-coder:14b"]


def test_pack_with_no_specialist_installed_falls_back_to_the_usual_council():
    generalists = [m for m in INSTALLED if "coder" not in m["model"] and "devstral" not in m["model"]]
    seats, specialists = inventory.pick_council_for_pack(generalists, 64 * GB, CODE)
    assert specialists == [] and names(seats) == names(inventory.pick_council(generalists, 64 * GB))


def test_a_lone_specialist_still_gets_a_real_debate():
    seats, specialists = inventory.pick_council_for_pack([model("qwen2.5-coder:7b", 5)], 64 * GB, CODE)
    assert specialists == ["qwen2.5-coder:7b"] and len(seats) == AUTO_COUNCIL_MIN


def test_suggestions_skip_installed_models_and_check_disk(monkeypatch):
    monkeypatch.setattr(
        inventory,
        "catalog",
        lambda num_ctx=0: [
            {"model": "qwen3-coder:30b", "size_bytes": 19 * GB, "est_bytes": 22 * GB, "fit": "fits"},
            {"model": "qwen2.5-coder:7b", "size_bytes": 5 * GB, "est_bytes": 6 * GB, "fit": "fits"},
        ],
    )
    monkeypatch.setattr(inventory, "free_disk_bytes", lambda: 10 * GB)
    out = inventory.pack_suggestions(CODE, [model("qwen2.5-coder:7b", 5)])
    assert [s["model"] for s in out] == ["qwen3-coder:30b"]
    assert out[0]["disk_ok"] is False  # 19 GB won't fit in 10 GB free
    monkeypatch.setattr(inventory, "free_disk_bytes", lambda: 100 * GB)
    out = inventory.pack_suggestions(CODE, [])
    assert [s["model"] for s in out] == ["qwen3-coder:30b", "qwen2.5-coder:7b"] and all(s["disk_ok"] for s in out)
    assert inventory.pack_suggestions(None, []) == []


def test_pack_model_preferences_are_validated():
    ok = packs.validate("p", {"name": "n", "description": "d", "guidance": "g", "models": {"prefer": [" Coder "]}})
    assert ok["models"] == {"prefer": ["coder"], "suggest": [], "seats": 4}
    for bad, problem in [
        ("coder", "must be an object"),
        ({"prefer": "coder"}, "list of model names"),
        ({"prefer": ["x"] * 13}, "at most 12"),
        ({"seats": 1}, "from 2 to 8"),
    ]:
        with pytest.raises(packs.PackError, match=problem):
            packs.validate("p", {"name": "n", "description": "d", "guidance": "g", "models": bad})


def test_the_code_review_pack_prefers_coding_models():
    review = packs.validate(
        "code-review", json.load(open(f"{packs.BUILTIN_PACKS_DIR}/code-review.json", encoding="utf-8"))
    )
    assert "coder" in review["models"]["prefer"] and review["models"]["suggest"]
    known = {m["model"] for m in json.load(open("backend/catalog.json"))["models"]}
    assert set(review["models"]["suggest"]) <= known  # every suggestion has a size to check before downloading


# ------------------------------------------------------------------ the health report


async def test_report_measures_debates_and_flags_rubber_stamping():
    eng = make_debate(FakeClient(lambda h, r, m: reply("AGREE")))
    await eng.post_user_message("Rust or Go?")
    await eng.task
    r = diagnostics.report(20)
    assert r["conundrums"] == 1 and r["answers"] == 1 and r["outcomes"] == {"consensus": 1}
    assert r["turns"] == 6 and r["stances_pct"]["agree"] == 100.0 and r["round1_agree_pct"] == 100.0
    assert any("round-1 turns already AGREE" in f for f in r["findings"])
    text = diagnostics.as_text(r)
    assert "Agent turns: 6" in text and "Findings:" in text


def test_report_with_no_conundrums():
    r = diagnostics.report()
    assert r["conundrums"] == 0 and "ask something first" in diagnostics.as_text(r)


def test_findings_flag_a_model_much_slower_than_the_rest():
    base = {"turns": 0, "stances_pct": {}, "models": [], "research": {}}
    fast = [{"model": f"m{i}", "calls": 10, "avg_seconds": 20.0, "turns": 5} for i in range(3)]
    slow = {"model": "slowpoke", "calls": 9, "avg_seconds": 160.0, "turns": 5}
    out = diagnostics.findings({**base, "models": [slow, *fast]})
    assert any(f.startswith("slowpoke averages 160.0s per call, 8.0×") for f in out)
    assert diagnostics.findings({**base, "models": fast}) == [
        "Nothing stands out: debates finish, models answer in format, and agents disagree when they should."
    ]


def test_doctor_prints_the_report(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get", lambda path, raw=False: "REPORT TEXT" if raw else {})
    assert cli.main(["doctor", "--last", "5"]) == 0
    assert capsys.readouterr().out.strip() == "REPORT TEXT"
