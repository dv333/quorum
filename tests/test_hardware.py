from backend.hardware import GB, estimate_model_bytes, fit_label, kv_cache_bytes, plan_council
from backend.inventory import pick_council, pick_researcher


def test_kv_cache_from_metadata():
    info = {
        "llama.block_count": 32,
        "llama.attention.head_count_kv": 8,
        "llama.embedding_length": 4096,
        "llama.attention.head_count": 32,
    }
    # 32 layers * 8 kv heads * (128 + 128) * 8192 ctx * 2 bytes = 1 GiB
    assert kv_cache_bytes(info, 8192, None) == 1 * GB


def test_kv_cache_fallback_heuristic():
    assert kv_cache_bytes({}, 8192, 10 * GB) == int(10 * GB * 0.06)


def test_estimate_and_fit():
    est = estimate_model_bytes(5 * GB, {}, 8192)
    assert est > 5 * GB
    assert fit_label(est, 40 * GB) == "fits"
    assert fit_label(est, 4 * GB) == "too_big"
    assert fit_label(None, 40 * GB) == "unknown"


def test_plan_modes_and_dedup():
    usable = 20 * GB
    same = [{"key": "1|a", "est_bytes": 9 * GB}] * 3
    assert plan_council(same, usable)["mode"] == "parallel"  # one model reused by three seats
    mixed = [
        {"key": "1|a", "est_bytes": 9 * GB},
        {"key": "1|b", "est_bytes": 9 * GB},
        {"key": "1|c", "est_bytes": 9 * GB},
    ]
    assert plan_council(mixed, usable)["mode"] == "sequential"
    assert plan_council([{"key": "1|x", "est_bytes": 30 * GB}], usable)["mode"] == "too_big"
    p = plan_council([{"key": "2|y", "est_bytes": None}], usable)
    assert p["mode"] == "parallel" and p["unknown_models"] == ["2|y"]


def _m(name, family, gb, fit="fits", chat=True):
    return {"model": name, "family": family, "est_bytes": int(gb * GB), "fit": fit, "chat": chat}


def test_pick_council_includes_every_runnable_local_model():
    models = [
        _m("qwen3.6", "qwen35moe", 22.2),
        _m("phi4", "phi3", 10.5),
        _m("deepseek-r1:8b", "qwen3", 6.5),
        _m("gemma3:4b", "gemma3", 4.7),
        _m("nomic-embed", "nomic-bert", 0.3, chat=False),
        _m("gpt-oss:120b", "gptoss", 69, fit="too_big"),
    ]
    seats = pick_council(models, int(36 * GB))
    # all four chat models, largest first, even though they don't fit in memory together (37.4 GB)
    assert [s["model"] for s in seats] == ["qwen3.6", "phi4", "deepseek-r1:8b", "gemma3:4b"]
    assert pick_researcher(seats)["model"] == "deepseek-r1:8b"


def test_pick_council_repeats_to_minimum_and_caps_at_agent_names():
    assert [s["model"] for s in pick_council([_m("qwen3:8b", "qwen3", 6)], int(36 * GB))] == ["qwen3:8b"] * 3
    assert pick_council([], int(36 * GB)) == []
    many = [_m(f"m{i}", f"f{i}", 1 + i) for i in range(12)]
    assert len(pick_council(many, int(36 * GB))) == 8
