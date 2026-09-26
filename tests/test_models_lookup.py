"""Finding any model in Ollama's library by name, and removing a downloaded one."""

import httpx
import pytest

from backend import providers


class FakeResponse:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = str(payload)

    def json(self):
        return self._payload


@pytest.fixture
def registry(monkeypatch):
    seen = {}

    async def get(self, url, headers=None):
        seen["url"] = url
        if url.endswith("/library/qwen3/manifests/30b"):
            return FakeResponse(200, {"layers": [{"size": 18_556_685_856}, {"size": 1506}]})
        return FakeResponse(404, {"errors": [{"code": "MANIFEST_UNKNOWN"}]})

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    return seen


async def test_a_library_model_reports_its_download_size(registry):
    assert await providers.lookup_model("qwen3:30b") == 18_556_687_362
    assert registry["url"] == "https://registry.ollama.ai/v2/library/qwen3/manifests/30b"


async def test_unknown_names_and_bad_input_find_nothing(registry):
    assert await providers.lookup_model("qwen3:999b") is None
    assert await providers.lookup_model("not a model") is None  # never sent to the registry
    await providers.lookup_model("someone/their-model")
    assert registry["url"].endswith("/someone/their-model/manifests/latest")


async def test_only_ollama_servers_can_remove_models():
    ep = providers.Endpoint(id=1, name="OpenAI", kind="openai_compat", base_url="https://api.openai.com/v1")
    with pytest.raises(providers.ProviderError):
        await providers.delete_model(ep, "gpt-4o")
