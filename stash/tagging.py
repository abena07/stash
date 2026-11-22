"""tag saved links by interest with claude haiku (#4), using the server's own key."""

import json
import logging
import re

import anthropic

log = logging.getLogger(__name__)

MODEL = "claude-haiku-5-5"
MAX_TAGS = 3
MAX_TAG_LENGTH = 24

SYSTEM = (
    "you tag links that someone saved to read later, so they can be grouped by interest. "
    f"give 1 to {MAX_TAGS} short topic tags: lowercase, 1 to 3 words, broad enough to be reused "
    "across many links (e.g. 'programming', 'design', 'personal finance'), not the article's title. "
    "when one of the person's existing tags fits, use it exactly as written instead of a new synonym."
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


def clean(tags: list[str]) -> list[str]:
    """lowercase, trimmed, de-duplicated, at most MAX_TAGS short tags."""
    out = []
    for tag in tags:
        tag = re.sub(r"\s+", " ", tag.strip().lower().lstrip("#"))
        if tag and len(tag) <= MAX_TAG_LENGTH and tag not in out:
            out.append(tag)
    return out[:MAX_TAGS]


async def tag_link(client: anthropic.AsyncAnthropic, url: str, title: str | None,
                   description: str | None, existing: list[str]) -> list[str]:
    """tags for one link. returns [] if claude declines or the request fails."""
    try:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=256,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=SYSTEM,
            messages=[{"role": "user", "content": build_prompt(url, title, description, existing)}],
        )
    except anthropic.AuthenticationError:
        log.warning("tagging skipped: the server's claude key was rejected")
        return []
    except anthropic.APIError as e:
        log.warning("tagging %s failed: %s", url, e)
        return []
    if response.stop_reason != "end_turn":
        log.info("no tags for %s: stop reason %s", url, response.stop_reason)
        return []
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return clean(json.loads(text)["tags"])
    except (ValueError, KeyError, TypeError):
        log.warning("tagging %s returned something unexpected", url)
        return []
