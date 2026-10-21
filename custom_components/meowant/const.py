"""Constants for the Meowant integration."""
from __future__ import annotations

from datetime import timedelta

DOMAIN = "meowant"

# --- Upstream API (TODO: fill in once captured from the Meowant app's traffic; see API.md) ---
API_BASE_URL = ""

# --- Coordinator ---
DEFAULT_UPDATE_INTERVAL = timedelta(seconds=60)
