"""Convenience launcher: python -m run [--gui|--cli]
Defaults to GUI mode.
"""
from __future__ import annotations
import argparse
import sys

from controller_sender import gui, main


def parse(argv=None):
    p = argparse.ArgumentParser()
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--gui", action="store_true", help="Launch GUI (default)")
    mode.add_argument("--cli", action="store_true", help="Run headless CLI")
    # Use parse_known_args so anything unknown is passed on to CLI module
    args, rest = p.parse_known_args(argv)
    args.rest = rest
    return args


def dispatch(args):
    if args.cli:
        main.main(args.rest)
    else:
        # Ignore rest for GUI
        gui.launch()


def main_entry():
    args = parse(sys.argv[1:])
    dispatch(args)

if __name__ == "__main__":  # pragma: no cover
    main_entry()
