"""``python -m ohs.server [options]``: the same as ``ohs app [options]``."""
from __future__ import annotations

import sys

from ohs.app.cli import main

if __name__ == "__main__":  # pragma: no cover - thin entry point
    sys.exit(main(["app", *sys.argv[1:]]))
