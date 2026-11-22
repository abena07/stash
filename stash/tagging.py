"""tag saved links by interest (#4), with the ai provider and key the server brought (#11)."""

import logging
import re

import aiohttp

from . import providers

log = logging.getLogger(__name__)

MAX_TAGS = 3
MAX_TAG_LENGTH = 24

SYSTEM = (
    "you tag links that someone saved to read later, so they can be grouped by interest. "
    f"give 1 to {MAX_TAGS} short topic tags: lowercase, 1 to 3 words, broad enough to be reused "
    "across many links (e.g. 'programming', 'design', 'personal finance'), not the article's title. "
    "when one of the person's existing tags fits, use it exactly as written instead of a new synonym. "
    'reply with only json like {"tags": ["programming", "git"]}.'
)

SCHEMA = {
    "type": "object",
    "properties": {"tags": {"type": "array", "items": {"type": "string"}}},
    "required": ["tags"],
    "additionalProperties": False,
}


def build_prompt(url: str, title: str | None, description: str | None, existing: list[str]) -> str:
    lines = [f"url: {url}"]
    if title:
        lines.append(f"title: {title}")
    if description:
        lines.append(f"description: {description}")
    lines.append(f"existing tags: {', '.join(existing) if existing else '(none yet)'}")
    return "\n".join(lines)


def clean(tags: list) -> list[str]:
    """lowercase, trimmed, de-duplicated, at most MAX_TAGS short tags."""
    out = []
    for tag in tags:
        if not isinstance(tag, str):
            continue
        tag = re.sub(r"\s+", " ", tag.strip().lower().lstrip("#"))
        if tag and len(tag) <= MAX_TAG_LENGTH and tag not in out:
            out.append(tag)
    return out[:MAX_TAGS]


async def tag_link(session: aiohttp.ClientSession, provider: providers.Provider, api_key: str, model: str,
                   url: str, title: str | None, description: str | None, existing: list[str]) -> list[str]:
    """tags for one link. returns [] if the model declines, the request fails or the reply is odd."""
    text = await providers.complete_json(
        session, provider, api_key, model, SYSTEM, build_prompt(url, title, description, existing), SCHEMA
    )
    data = providers.parse_json(text)
    if data is None or not isinstance(data.get("tags"), list):
        if text is not None:
            log.warning("tagging %s with %s returned something unexpected", url, provider.id)
        return []
    return clean(data["tags"])
