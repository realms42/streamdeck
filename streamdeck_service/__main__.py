"""Entry point: python -m streamdeck_service [options]"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Drive an Elgato Stream Deck from a YAML config file."
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        type=Path,
        metavar="FILE",
        help="Path to the YAML config file (default: config.yaml)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    # Quieten noisy watchdog internals unless we're in DEBUG mode
    if args.log_level != "DEBUG":
        logging.getLogger("watchdog").setLevel(logging.WARNING)

    from .service import StreamDeckService

    StreamDeckService(args.config).run()


if __name__ == "__main__":
    main()
