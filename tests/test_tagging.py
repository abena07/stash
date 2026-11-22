import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import anthropic
import pytest
from cryptography.fernet import Fernet

from stash import drops, keys, tagging

SECRET = Fernet.generate_key().decode()


# ---- key storage (#11) ----

def test_keys_round_trip_encrypted():
    blob = keys.encrypt(SECRET, "sk-ant-api03-abcdefghijklmnop")
    assert b"sk-ant" not in blob
    assert keys.decrypt(SECRET, blob) == "sk-ant-api03-abcdefghijklmnop"


def test_key_from_a_different_secret_reads_as_missing():
    blob = keys.encrypt(SECRET, "sk-ant-api03-abcdefghijklmnop")
    assert keys.decrypt(Fernet.generate_key().decode(), blob) is None


def test_no_secret_means_no_key_store():
    assert keys.decrypt(None, b"anything") is None
    with pytest.raises(keys.KeyStoreUnavailable):
        keys.encrypt(None, "sk-ant-api03-abcdefghijklmnop")


@pytest.mark.parametrize(
    "text, ok",
    [("sk-ant-api03-abcdefghijklmnop", True), ("sk-ant-", False), ("hello there", False),
     ("sk-proj-abcdefghijklmnopqrstu", False), ("sk-ant-api03 abcdefghijklmnop", False)],
)
def test_looks_like_key(text, ok):
    assert keys.looks_like_key(text) is ok


# ---- tagging (#4) ----

def test_clean_tags():
    assert tagging.clean(["Programming", " #Design ", "programming", "x" * 40, "", "ai", "extra"]) == [
        "programming", "design", "ai",
    ]


def test_prompt_includes_existing_tags_for_reuse():
    prompt = tagging.build_prompt("https://a.com", "A title", None, ["design", "programming"])
    assert prompt == "url: https://a.com\ntitle: A title\nexisting tags: design, programming"
    assert "(none yet)" in tagging.build_prompt("https://a.com", None, None, [])


class FakeMessages:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def fake_client(text='{"tags": ["Programming", "git"]}', stop_reason="end_turn", error=None):
    response = SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)])
    return SimpleNamespace(messages=FakeMessages(response, error))


def test_tag_link_uses_haiku_with_structured_output():
    client = fake_client()
    tags = asyncio.run(tagging.tag_link(client, "https://jvns.ca/a", "Inside .git", None, []))
    assert tags == ["programming", "git"]
    call = client.messages.calls[0]
    assert call["model"] == "claude-haiku-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["output_config"]["effort"] == "low"


def test_tag_link_gives_up_quietly():
    refused = fake_client(stop_reason="refusal")
    garbled = fake_client(text="not json")
    failing = fake_client(error=anthropic.APIConnectionError(request=None))
    for client in (refused, garbled, failing):
        assert asyncio.run(tagging.tag_link(client, "https://a.com", None, None, [])) == []


def test_picks_show_tags():
    link = drops.Candidate(1, "https://jvns.ca/a", "Inside .git", 5, 0,
                           datetime(2026, 11, 1, tzinfo=timezone.utc), ("programming", "git"))
    assert drops.format_pick(1, link) == "**1. Inside .git** · 5 min read · programming, git\nhttps://jvns.ca/a"
