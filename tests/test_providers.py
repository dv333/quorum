from backend import db
from backend.hardware import GB
from backend.inventory import _public_endpoint, pick_council
from backend.providers import Endpoint, _v1


def ep(url, key=None):
    return Endpoint(1, "x", url, "openai_compat", True, key)


def test_v1_suffix_only_for_bare_hosts():
    assert _v1(ep("http://localhost:1234"), "/models") == "http://localhost:1234/v1/models"
    assert _v1(ep("https://api.openai.com/v1"), "/models") == "https://api.openai.com/v1/models"
    assert (
        _v1(ep("https://openrouter.ai/api/v1"), "/chat/completions") == "https://openrouter.ai/api/v1/chat/completions"
    )
    assert _v1(ep("https://generativelanguage.googleapis.com/v1beta/openai"), "/models").endswith(
        "/v1beta/openai/models"
    )


def test_local_detection_and_auth_headers():
    assert ep("http://localhost:11434").is_local and ep("http://127.0.0.1:8080").is_local
    assert not ep("https://api.openai.com/v1").is_local
    assert "Authorization" not in ep("http://localhost:1234").headers()
    assert ep("https://api.openai.com/v1", "sk-test").headers()["Authorization"] == "Bearer sk-test"
    assert ep("https://api.anthropic.com/v1", "sk-ant").headers()["x-api-key"] == "sk-ant"


def test_api_key_never_exposed():
    db.connect(":memory:")
    pub = _public_endpoint(ep("https://api.openai.com/v1", "sk-secret-abcd1234"), True, None, 3)
    assert "api_key" not in pub and "sk-secret" not in str(pub)
    assert pub["has_key"] and pub["key_hint"] == "…1234" and pub["local"] is False


def test_auto_council_skips_cloud_models():
    models = [
        {"model": "gpt-x", "family": "openai", "est_bytes": None, "fit": "cloud", "chat": True, "local": False},
        {"model": "qwen3:8b", "family": "qwen3", "est_bytes": int(6 * GB), "fit": "fits", "chat": True, "local": True},
    ]
    assert {s["model"] for s in pick_council(models, int(36 * GB))} == {"qwen3:8b"}


def test_starter_pack_scales_with_memory():
    from backend.setup import starter_pack

    assert starter_pack(int(36 * GB)) == ["qwen3:14b", "gemma3:12b", "llama3.1:8b"]
    assert starter_pack(int(16 * GB))[0] == "qwen3:8b"
    assert starter_pack(int(6 * GB))[0] == "qwen3:4b"
    for pack in (starter_pack(int(g * GB)) for g in (6, 16, 36)):
        assert len({m.split(":")[0].rstrip("0123456789.") for m in pack}) == 3  # three families
