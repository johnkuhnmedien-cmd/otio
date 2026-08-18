#!/usr/bin/env python3
"""CLI: python scripts/otio_app_ctl.py restart --pull"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from otio_app.app_ctl import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
