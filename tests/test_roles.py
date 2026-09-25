import json

import pytest

from backend import db
from backend.engine import required_roles, settle_roles
from backend.providers import Chunk
from tests.test_engine import FakeClient, make_debate, reply

HANDLES = ["Otter", "Panda", "Koala", "Penguin", "Hedgehog"]


@pytest.fixture(autouse=True)
def memory_db():
    db.connect(":memory:")
    yield


def names(roles):
    return {h: r["role"] for h, r in roles.items()}


def test_the_chairs_roles_are_kept_when_they_cover_what_is_required():
    proposed = {
        "otter": {"role": "Tax advisor", "focus": "deductions"},
        "panda": {"role": "Skeptic", "focus": ""},
        "koala": {"role": "Pragmatist", "focus": ""},
        "penguin": {"role": "User advocate", "focus": ""},
        "hedgehog": {"role": "Realtor", "focus": "local market"},
    }
    roles = settle_roles(HANDLES, proposed, required_roles(5))
    assert names(roles) == {
        "Otter": "Tax advisor",
        "Panda": "Skeptic",
        "Koala": "Pragmatist",
        "Penguin": "User advocate",
        "Hedgehog": "Realtor",
    }
    assert roles["Otter"]["focus"] == "deductions"


def test_missing_and_duplicate_roles_are_filled_and_required_roles_are_forced_in():
    proposed = {
        "otter": {"role": "Economist", "focus": ""},
        "panda": {"role": "Economist", "focus": ""},  # duplicate: dropped
        "koala": {"role": "Historian", "focus": ""},
    }
    roles = settle_roles(HANDLES, proposed, required_roles(5))
    got = list(names(roles).values())
    assert len(set(got)) == 5  # all distinct
    for need in ("Skeptic", "Pragmatist", "User advocate"):
        assert need in got
    assert roles["Otter"]["role"] == "Economist" and roles["Koala"]["role"] == "Historian"


def test_nothing_usable_from_the_chair_gives_the_default_mix():
    roles = settle_roles(HANDLES[:3], {}, required_roles(3))
    assert names(roles) == {"Otter": "Domain expert", "Panda": "Skeptic", "Koala": "Pragmatist"}


def test_a_critic_by_another_name_counts_as_the_skeptic():
    proposed = {"otter": {"role": "Devil's advocate", "focus": ""}, "panda": {"role": "Chemist", "focus": ""}}
    assert names(settle_roles(HANDLES[:2], proposed, required_roles(2))) == {
        "Otter": "Devil's advocate",
        "Panda": "Chemist",
    }


def test_required_roles_scale_with_the_council():
    assert required_roles(2) == ["Skeptic"]
    assert required_roles(3) == ["Skeptic", "Pragmatist"]
    assert required_roles(8) == ["Skeptic", "Pragmatist", "User advocate"]


class RoleChair(FakeClient):
    def __init__(self, turn_fn, roles_reply):
        super().__init__(turn_fn)
        self.roles_reply = roles_reply

    async def stream(self, endpoint, model, messages, **kw):
        if "assign debate roles" in messages[0]["content"]:
            self.calls.append((model, messages, kw))
            yield Chunk("content", self.roles_reply)
            yield Chunk("done", stats={"tokens": 5})
            return
        async for chunk in super().stream(endpoint, model, messages, **kw):
            yield chunk


async def test_roles_reach_every_agent_and_each_message_remembers_its_role():
    roles_reply = json.dumps(
        {
            "roles": [
                {"agent": "Otter", "role": "Pediatrician", "focus": "child safety"},
                {"agent": "Panda", "role": "Skeptic", "focus": "weak evidence"},
                {"agent": "Koala", "role": "Pragmatist", "focus": "cost"},
            ]
        }
    )
    client = RoleChair(lambda h, r, m: reply("AGREE"), roles_reply)
    eng = make_debate(client)
    await eng.post_user_message("Is screen time bad for toddlers?")
    await eng.task
    assert {s["handle"]: s["role"] for s in eng.seats()} == {
        "Otter": "Pediatrician",
        "Panda": "Skeptic",
        "Koala": "Pragmatist",
    }
    otter_system = client.turn_calls("Otter")[0][0][0]["content"]
    assert "Your role in this debate: Pediatrician. child safety" in otter_system
    assert "Panda (Skeptic), Koala (Pragmatist)" in otter_system
    snap = eng.snapshot()
    turns = [m for m in snap["messages"] if m["author_kind"] == "seat"]
    assert turns and all(m["meta"]["role"] for m in turns)
    row = next(m for m in snap["messages"] if (m["meta"] or {}).get("kind") == "roles")
    assert [r["role"] for r in row["meta"]["roles"]] == ["Pediatrician", "Skeptic", "Pragmatist"]
    assert snap["seats"][0]["role"] == "Pediatrician"
    assert db.query("SELECT kind FROM usage WHERE kind = 'roles'")


async def test_an_unusable_reply_still_gives_every_agent_a_role():
    client = RoleChair(lambda h, r, m: reply("AGREE"), "no json here")
    eng = make_debate(client)
    await eng.post_user_message("Rust or Go?")
    await eng.task
    assert [s["role"] for s in eng.seats()] == ["Domain expert", "Skeptic", "Pragmatist"]
