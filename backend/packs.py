"""Topic packs: small JSON files that set up a kind of debate (a question starter, a focus and debate guidance).

Built-in packs live in backend/packs/. Your own go in data/packs/ (or QUORUM_PACKS_DIR); a file with the same
name as a built-in pack replaces it. See docs/PACKS.md for the format.
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from .config import BUILTIN_PACKS_DIR, USER_PACKS_DIR

log = logging.getLogger("quorum.packs")

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
# field: (required, max length)
FIELDS = {
    "name": (True, 40),
    "description": (True, 200),
    "emoji": (False, 8),
    "prompt": (False, 300),
    "focus": (False, 200),
    "guidance": (True, 800),
}


class PackError(ValueError):
    pass


def validate(pack_id: str, raw: Any) -> Dict[str, Any]:
    """Return a clean pack, or raise PackError explaining what's wrong."""
    if not ID_RE.match(pack_id):
        raise PackError("the file name must be lowercase letters, digits and dashes (for example code-review.json)")
    if not isinstance(raw, dict):
        raise PackError("a pack must be a JSON object")
    pack: Dict[str, Any] = {"id": pack_id}
    for field, (required, limit) in FIELDS.items():
        value = raw.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            if required:
                raise PackError(f'"{field}" is required')
            continue
        if not isinstance(value, str):
            raise PackError(f'"{field}" must be text')
        if len(value) > limit:
            raise PackError(f'"{field}" is longer than {limit} characters')
        # The question starter keeps its trailing space so the cursor lands after it
        pack[field] = value if field == "prompt" else value.strip()
    if raw.get("models") is not None:
        pack["models"] = _validate_models(raw["models"])
    return pack


def _validate_models(raw: Any) -> Dict[str, Any]:
    """Which installed models suit this pack ("prefer": name fragments), what to suggest when none are installed
    ("suggest": Ollama model names), and how many seats the council gets when specialists are found ("seats")."""
    if not isinstance(raw, dict):
        raise PackError('"models" must be an object with "prefer", "suggest" and "seats"')
    out: Dict[str, Any] = {}
    for key, most in (("prefer", 12), ("suggest", 5)):
        items = raw.get(key, [])
        if not isinstance(items, list) or not all(isinstance(x, str) and 0 < len(x.strip()) <= 60 for x in items):
            raise PackError(f'"models.{key}" must be a list of model names')
        if len(items) > most:
            raise PackError(f'"models.{key}" can list at most {most} models')
        out[key] = [x.strip().lower() for x in items]
    seats = raw.get("seats", 4)
    if not isinstance(seats, int) or not 2 <= seats <= 8:
        raise PackError('"models.seats" must be a whole number from 2 to 8')
    out["seats"] = seats
    return out


def _load_dir(path: str, builtin: bool) -> Dict[str, Dict[str, Any]]:
    found: Dict[str, Dict[str, Any]] = {}
    if not os.path.isdir(path):
        return found
    for name in sorted(os.listdir(path)):
        if not name.endswith(".json"):
            continue
        pack_id = name[: -len(".json")]
        try:
            with open(os.path.join(path, name), encoding="utf-8") as f:
                pack = validate(pack_id, json.load(f))
        except (OSError, json.JSONDecodeError, PackError) as e:
            log.warning("skipping topic pack %s: %s", os.path.join(path, name), e)
            continue
        pack["builtin"] = builtin
        found[pack_id] = pack
    return found


# Built-in packs appear in this order; your own come after them, alphabetically
ORDER = ["compare", "plan", "check-claim", "explain", "code-review", "decision", "brainstorm"]


def load_all(user_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    packs = _load_dir(BUILTIN_PACKS_DIR, builtin=True)
    packs.update(_load_dir(user_dir or USER_PACKS_DIR, builtin=False))
    rank = {pid: i for i, pid in enumerate(ORDER)}
    return sorted(packs.values(), key=lambda p: (rank.get(p["id"], len(ORDER)), p["name"].lower()))


def get(pack_id: str) -> Optional[Dict[str, Any]]:
    return next((p for p in load_all() if p["id"] == pack_id), None)
