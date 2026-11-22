import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp import web
from cryptography.fernet import Fernet

from stash import drops, keys, providers, tagging

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


# ---- tagging (#4) ----

def test_clean_tags():
    assert tagging.clean(["Programming", " #Design ", "programming", "x" * 40, "", 7, "ai", "extra"]) == [
        "programming", "design", "ai",
    ]


def test_prompt_includes_existing_tags_for_reuse():
    prompt = tagging.build_prompt("https://a.com", "A title", None, ["design", "programming"])
    assert prompt == "url: https://a.com\ntitle: A title\nexisting tags: design, programming"
    assert "(none yet)" in tagging.build_prompt("https://a.com", None, None, [])


def test_picks_show_tags():
    link = drops.Candidate(1, "https://jvns.ca/a", "Inside .git", 5, 0,
                           datetime(2026, 11, 1, tzinfo=timezone.utc), ("programming", "git"))
    assert drops.format_pick(1, link) == "**1. Inside .git** · 5 min read · programming, git\nhttps://jvns.ca/a"


# ---- providers (#11): a fake openai-compatible api on localhost ----

GOOD_KEY = "sk-good"


def fake_api(reply='{"tags": ["Programming", "git"]}', finish="stop", models=("cheap-mini", "big-model")):
    seen = []

    def authed(request):
        return request.headers.get("Authorization") == f"Bearer {GOOD_KEY}"

    async def list_models(request):
        if not authed(request):
            return web.json_response({"error": "bad key"}, status=401)
        return web.json_response({"data": [{"id": f"models/{m}"} for m in models]})

    async def chat(request):
        if not authed(request):
            return web.json_response({"error": "bad key"}, status=401)
        seen.append(await request.json())
        return web.json_response({"choices": [{"finish_reason": finish, "message": {"content": reply}}]})

    app = web.Application()
    app.router.add_get("/models", list_models)
    app.router.add_post("/chat/completions", chat)
    return app, seen


def against(app, fn):
    """run fn(session, provider) against the fake api."""
    async def go():
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        provider = providers.Provider("fake", "Fake AI", "cheap-mini", f"http://127.0.0.1:{port}")
        try:
            async with aiohttp.ClientSession() as session:
                return await fn(session, provider)
        finally:
            await runner.cleanup()
    return asyncio.run(go())


def test_every_provider_has_a_fixed_https_url():
    assert set(providers.PROVIDERS) == {"claude", "openai", "gemini", "deepseek", "openrouter"}
    for p in providers.PROVIDERS.values():
        assert p.base_url is None or p.base_url.startswith("https://")


def test_check_accepts_a_good_key_and_model():
    app, _ = fake_api()
    against(app, lambda s, p: providers.check(s, p, GOOD_KEY, "cheap-mini"))  # no error


def test_check_rejects_a_bad_key():
    app, _ = fake_api()
    with pytest.raises(providers.ProviderError, match="didn't work"):
        against(app, lambda s, p: providers.check(s, p, "sk-wrong", "cheap-mini"))


def test_check_suggests_models_when_the_model_is_wrong():
    app, _ = fake_api()
    with pytest.raises(providers.ProviderError, match="`nope` isn't available.*`cheap-mini`"):
        against(app, lambda s, p: providers.check(s, p, GOOD_KEY, "nope"))


def test_tagging_through_an_openai_compatible_api():
    app, seen = fake_api()
    tags = against(app, lambda s, p: tagging.tag_link(s, p, GOOD_KEY, "cheap-mini", "https://jvns.ca/a",
                                                       "Inside .git", None, ["programming"]))
    assert tags == ["programming", "git"]
    body = seen[0]
    assert body["model"] == "cheap-mini" and body["response_format"] == {"type": "json_object"}
    assert "json" in body["messages"][0]["content"]  # json mode needs the word json in the prompt


@pytest.mark.parametrize("reply, finish", [("   ", "stop"), ("not json", "stop"), ('{"tags": "x"}', "stop"),
                                           ('{"tags": ["a"]}', "length")])
def test_odd_replies_mean_no_tags(reply, finish):
    app, _ = fake_api(reply=reply, finish=finish)
    tags = against(app, lambda s, p: tagging.tag_link(s, p, GOOD_KEY, "cheap-mini", "https://a.com", None, None, []))
    assert tags == []


def test_tagging_with_a_revoked_key_gives_no_tags():
    app, _ = fake_api()
    tags = against(app, lambda s, p: tagging.tag_link(s, p, "sk-wrong", "cheap-mini", "https://a.com", None, None, []))
    assert tags == []


class FakeAnthropic:
    calls = []

    def __init__(self, **kwargs):
        self.messages = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def create(self, **kwargs):
        FakeAnthropic.calls.append(kwargs)
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text='{"tags": ["design"]}')])


def test_claude_uses_the_sdk_with_structured_output(monkeypatch):
    monkeypatch.setattr(providers.anthropic, "AsyncAnthropic", FakeAnthropic)
    claude = providers.PROVIDERS["claude"]

    async def go():
        async with aiohttp.ClientSession() as s:
            return await tagging.tag_link(s, claude, "sk-ant-x", "claude-haiku-5-5", "https://a.com", None, None, [])

    assert asyncio.run(go()) == ["design"]
    call = FakeAnthropic.calls[-1]
    assert call["model"] == "claude-haiku-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
