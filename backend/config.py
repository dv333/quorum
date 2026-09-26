"""Configuration for Quorum. Every value can be overridden via environment / .env."""

import os

from dotenv import load_dotenv

load_dotenv()


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


# Backend port (8000/8001 are used by other apps on the author's machine)
PORT = _env_int("LLC_PORT", 8002)

# SQLite database file
DB_PATH = os.getenv("LLC_DB_PATH", "data/council.db")

# Topic packs: the ones that ship with Quorum, and your own (JSON files; the same id overrides a built-in one)
BUILTIN_PACKS_DIR = os.path.join(os.path.dirname(__file__), "packs")
USER_PACKS_DIR = os.getenv("QUORUM_PACKS_DIR", "data/packs")

# Endpoints auto-registered on first start: (name, base_url, kind)
DEFAULT_ENDPOINTS = [
    ("Ollama", os.getenv("OLLAMA_URL", "http://localhost:11434"), "ollama"),
    ("LM Studio", os.getenv("LMSTUDIO_URL", "http://localhost:1234"), "openai_compat"),
    ("llama.cpp", os.getenv("LLAMACPP_URL", "http://localhost:8080"), "openai_compat"),
]

# Memory kept free for the OS and other apps when deciding what fits
MEMORY_RESERVE_GB = _env_float("LLC_MEMORY_RESERVE_GB", 8.0)

APP_NAME = "Quorum"

# Debate defaults ("just type and go")
DEFAULT_MAX_ROUNDS = _env_int("LLC_MAX_ROUNDS", 3)  # used until the chair sizes the debate
MAX_ROUNDS_LIMIT = 10
DEFAULT_AUTOPILOT = True
AUTO_COUNCIL_MAX = 8  # every installed model joins, up to one per agent name
AUTO_COUNCIL_MIN = 3  # repeat models if fewer distinct ones fit
INTAKE_MAX_QUESTIONS = 3  # the chair asks at most this many clarifying questions
MIN_ROUNDS_FOR_CONSENSUS = _env_int("LLC_MIN_ROUNDS", 2)
MAX_SEATS = 8
MIN_SEATS = 2
DEFAULT_NUM_CTX = _env_int("LLC_NUM_CTX", 8192)

# Fraction of the smallest context window the transcript may use before older
# rounds are compressed into the rolling summary
CONTEXT_BUDGET_FRACTION = 0.7
# A long prompt (the final answer with research and key studies) gets a bigger context so the reply still fits
REPLY_RESERVE_TOKENS = 2048
MAX_NUM_CTX = _env_int("LLC_MAX_NUM_CTX", 16384)

# Per-request timeout for a single model turn (local models can be slow to load)
REQUEST_TIMEOUT = _env_float("LLC_REQUEST_TIMEOUT", 600.0)

# Web research via Firecrawl (self-hosted by default; a cloud API key can be set in the UI)
FIRECRAWL_URL = os.getenv("FIRECRAWL_URL", "http://localhost:3002")
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY", "")
FIRECRAWL_CLOUD_URL = "https://api.firecrawl.dev"
RESEARCH_TIMEOUT = _env_float("LLC_RESEARCH_TIMEOUT", 90.0)
RESEARCH_MAX_QUERIES = 3  # searches the Researcher runs per request
RESEARCH_RESULTS_PER_QUERY = 3
RESEARCH_MAX_SOURCES = 5  # pages it reads before writing a brief
RESEARCH_PAGE_CHARS = 3500  # characters kept from each page (longer excerpts keep caveats and context)
RESEARCH_MAX_CLAIMS = 6  # material claims checked before the answer is written
RESEARCH_SOURCES_PER_CLAIM = 3
RESEARCH_REQUESTS_PER_ROUND = _env_int("LLC_RESEARCH_PER_ROUND", 3)  # agent requests honored per round

# Cloud providers with OpenAI-compatible chat endpoints (base URLs used as given)
PROVIDER_PRESETS = [
    {
        "id": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "key_url": "https://platform.openai.com/api-keys",
    },
    {
        "id": "anthropic",
        "name": "Anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "key_url": "https://console.anthropic.com/settings/keys",
    },
    {
        "id": "gemini",
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "key_url": "https://aistudio.google.com/apikey",
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "key_url": "https://openrouter.ai/keys",
    },
    {
        "id": "groq",
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "key_url": "https://console.groq.com/keys",
    },
    {
        "id": "mistral",
        "name": "Mistral",
        "base_url": "https://api.mistral.ai/v1",
        "key_url": "https://console.mistral.ai/api-keys",
    },
    {
        "id": "together",
        "name": "Together AI",
        "base_url": "https://api.together.xyz/v1",
        "key_url": "https://api.together.ai/settings/api-keys",
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "key_url": "https://platform.deepseek.com/api_keys",
    },
]

# Priorities offered in the UI; they steer the agents and the chair
DEFAULT_CRITERIA = ["Accuracy", "Reasoning", "Practicality", "Clarity", "Creativity", "Code quality"]

# Anonymous handles assigned to seats, in order. Neutral, friendly animals: models see these
# names too, so avoid ones with strong connotations (owl = wise, fox = sly).
HANDLES = ["Otter", "Panda", "Koala", "Penguin", "Hedgehog", "Bunny", "Turtle", "Dolphin"]
SEAT_COLORS = ["otter", "panda", "koala", "penguin", "hedgehog", "bunny", "turtle", "dolphin"]
RESEARCHER_NAME = "Beagle"
