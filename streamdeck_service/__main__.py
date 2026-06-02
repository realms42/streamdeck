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
    parser.add_argument(
        "--retry",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="If no deck is found at startup, poll every SECONDS until one appears "
        "(default: 0, meaning exit immediately).",
    )
    parser.add_argument(
        "--list-decks",
        action="store_true",
        help="List connected Stream Decks (index, serial, type, key count) and exit.",
    )
    return parser.parse_args()


def _print_decks() -> int:
    """Print connected decks; return a process exit code."""
    from .service import list_decks

    try:
        decks = list_decks()
    except Exception as exc:  # hardware/transport errors shouldn't traceback
        print(f"Could not enumerate decks: {exc}", file=sys.stderr)
        return 1

    if not decks:
        print("No Stream Decks found.")
        return 0

    print(f"Found {len(decks)} Stream Deck(s):")
    for d in decks:
        print(f"  [{d['index']}] {d['type']}  serial={d['serial']}  keys={d['keys']}")
    return 0


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

    if args.list_decks:
        sys.exit(_print_decks())

    from .service import StreamDeckService

    StreamDeckService(args.config, retry=args.retry).run()


if __name__ == "__main__":
    main()
