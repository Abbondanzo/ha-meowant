"""Makes custom_components/meowant/api.py importable for tests without
installing homeassistant. See _meowant_dev_import.py at the repo root for
how/why.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _meowant_dev_import import load_meowant_api  # noqa: E402

load_meowant_api()
