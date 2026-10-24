import logging

from . import config
from .bot import StashBot


def main() -> None:
    cfg = config.load()
    logging.basicConfig(
        level=cfg.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    StashBot(cfg).run(cfg.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
