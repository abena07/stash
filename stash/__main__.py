import logging
import sys

from . import config
from .bot import StashBot

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(level: str) -> None:
    """info and below to stdout, warnings and errors to stderr.

    railway marks every stderr line as an error, so routine logs go to stdout
    and only real problems show up red.
    """
    out = logging.StreamHandler(sys.stdout)
    out.addFilter(lambda record: record.levelno < logging.WARNING)
    err = logging.StreamHandler(sys.stderr)
    err.setLevel(logging.WARNING)
    for handler in (out, err):
        handler.setFormatter(logging.Formatter(FORMAT))
    logging.basicConfig(level=level, handlers=[out, err])


def main() -> None:
    cfg = config.load()
    setup_logging(cfg.log_level)
    StashBot(cfg).run(cfg.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
