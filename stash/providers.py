"""the ai providers a server can bring its own key for (#11).

claude goes through the anthropic sdk. the others all speak openai's chat
completions format, so one http client covers them. base urls are fixed
here on purpose: a server can't point the bot at an address of its choosing.
"""

import json
import logging
from dataclasses import dataclass

import aiohttp
import anthropic

log = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=30)


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    default_model: str  # a cheap model; /setkey checks it exists before saving
    base_url: str | None = None  # openai-compatible api root; None for claude
    key_check_path: str = "/models"  # an authenticated GET that costs nothing
    console: str = ""


PROVIDERS = {
    p.id: p
    for p in [
        Provider("claude", "Claude (Anthropic)", "claude-haiku-5-5", console="console.anthropic.com"),
        Provider("openai", "OpenAI", "gpt-6-luna", "https://api.openai.com/v1",
                 console="platform.openai.com"),
        Provider("gemini", "Gemini (Google)", "gemini-3.1-flash-lite",
                 "https://generativelanguage.googleapis.com/v1beta/openai", console="aistudio.google.com"),
        Provider("deepseek", "DeepSeek", "deepseek-v4-flash", "https://api.deepseek.com",
                 console="platform.deepseek.com"),
        Provider("openrouter", "OpenRouter (many models, one key)", "google/gemini-3.1-flash-lite",
                 "https://openrouter.ai/api/v1", key_check_path="/key", console="openrouter.ai"),
    ]
}

SUGGEST_WORDS = ("haiku", "mini", "nano", "flash", "lite", "luna", "chat")


class ProviderError(Exception):
    """a plain reason, shown to whoever ran /setkey."""


async def check(session: aiohttp.ClientSession, provider: Provider, api_key: str, model: str) -> None:
    """raise ProviderError unless the key works and can use `model`. no tokens are spent."""
    available = await _list_models(session, provider, api_key)
    if model not in available:
        raise ProviderError(
            f"`{model}` isn't available with that key. some that are: "
            + ", ".join(f"`{m}`" for m in suggest(available))
        )


def suggest(models: list[str], n: int = 8) -> list[str]:
    cheap = [m for m in models if any(w in m.lower() for w in SUGGEST_WORDS)]
    return (cheap or models)[:n]


async def complete_json(session: aiohttp.ClientSession, provider: Provider, api_key: str, model: str,
                        system: str, prompt: str, schema: dict) -> str | None:
    """one short json reply, or None on any failure (logged, never raised)."""
    try:
        if provider.base_url is None:
            return await _claude(api_key, model, system, prompt, schema)
        return await _openai_compatible(session, provider, api_key, model, system, prompt)
    except ProviderError as e:
        log.warning("%s request failed: %s", provider.id, e)
    except (aiohttp.ClientError, TimeoutError) as e:
        log.warning("%s request failed: %r", provider.id, e)
    except anthropic.APIError as e:
        log.warning("claude request failed: %s", e)
    return None


async def _list_models(session, provider: Provider, api_key: str) -> list[str]:
    if provider.base_url is None:
        client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=1, timeout=15)
        try:
            return [m.id async for m in client.models.list(limit=100)]
        except anthropic.AuthenticationError:
            raise ProviderError("that key didn't work. check it was copied in full")
        except anthropic.PermissionDeniedError:
            raise ProviderError("that key doesn't have permission to use the api")
        except anthropic.APIConnectionError:
            raise ProviderError("i couldn't reach anthropic to check the key. try again in a minute")
        except anthropic.APIStatusError as e:
            raise ProviderError(f"anthropic returned an error ({e.status_code}). try again in a minute")
        finally:
            await client.close()

    await _get(session, provider, api_key, provider.key_check_path)  # proves the key works
    data = await _get(session, provider, api_key, "/models")
    # gemini's ids come back as "models/gemini-…"
    return sorted(m["id"].removeprefix("models/") for m in data.get("data", []) if "id" in m)


async def _get(session, provider: Provider, api_key: str, path: str) -> dict:
    try:
        async with session.get(provider.base_url + path, headers=_auth(api_key), timeout=TIMEOUT) as resp:
            if resp.status in (401, 403):
                raise ProviderError("that key didn't work. check it was copied in full")
            if resp.status != 200:
                raise ProviderError(f"{provider.name} returned an error ({resp.status}). try again in a minute")
            return await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError):
        raise ProviderError(f"i couldn't reach {provider.name} to check the key. try again in a minute")


async def _openai_compatible(session, provider: Provider, api_key: str, model: str,
                             system: str, prompt: str) -> str | None:
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
    }
    async with session.post(provider.base_url + "/chat/completions", headers=_auth(api_key),
                            json=body, timeout=TIMEOUT) as resp:
        if resp.status != 200:
            raise ProviderError(f"http {resp.status}: {(await resp.text())[:200]}")
        data = await resp.json(content_type=None)
    choice = (data.get("choices") or [{}])[0]
    if choice.get("finish_reason") not in ("stop", None):
        log.info("%s stopped early: %s", provider.id, choice.get("finish_reason"))
        return None
    return (choice.get("message") or {}).get("content")


async def _claude(api_key: str, model: str, system: str, prompt: str, schema: dict) -> str | None:
    async with anthropic.AsyncAnthropic(api_key=api_key, max_retries=2, timeout=30) as client:
        response = await client.messages.create(
            model=model,
            max_tokens=256,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
    if response.stop_reason != "end_turn":
        log.info("claude stopped early: %s", response.stop_reason)
        return None
    return next((b.text for b in response.content if b.type == "text"), None)


def _auth(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


def parse_json(text: str | None) -> dict | None:
    if not text or not text.strip():
        return None  # deepseek blanks out replies that weren't valid json
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None
