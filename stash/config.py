import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    discord_token: str
    database_url: str
    dev_guild_id: int | None
    log_level: str
    alert_webhook_url: str | None = None


def load() -> Config:
    missing = [k for k in ("DISCORD_TOKEN", "DATABASE_URL") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"missing env vars: {', '.join(missing)} (see .env.example)")
    dev_guild = os.environ.get("DEV_GUILD_ID")
    return Config(
        discord_token=os.environ["DISCORD_TOKEN"],
        database_url=os.environ["DATABASE_URL"],
        dev_guild_id=int(dev_guild) if dev_guild else None,
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        alert_webhook_url=os.environ.get("ALERT_WEBHOOK_URL") or None,
    )
