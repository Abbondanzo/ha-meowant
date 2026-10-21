"""Constants for the Meowant integration."""
from __future__ import annotations

from datetime import timedelta

DOMAIN = "meowant"

# --- Meowant tenant on the UBTech AirPet cloud (see API.md "Tenants") ---
# Static, app-level constants shipped in every copy of the AIR PET app -- NOT
# user secrets. Don't confuse them with the user's own email/password.
DEFAULT_BASE_URL = "https://apis.airrobo-home.com"
DEFAULT_APP_ID = "970010016"
DEFAULT_APP_KEY = "7b5cde0a41bf4588983e7e1685492b29"
DEFAULT_PRODUCT = "97001"

USER_AGENT = "okhttp/4.10.0"

# Matches the AIR PET app.
LOGIN_METHOD = "PUT"

# accountType values the login endpoint accepts (JSON numbers); tried in order.
ACCOUNT_TYPE_EMAIL = 1
ACCOUNT_TYPE_PHONE = 0

# Login areaCode: the account's ISO country code, chosen at signup in the app.
DEFAULT_AREA_CODE = "US"

# Re-login this long before the token's stated expiry.
TOKEN_REFRESH_MARGIN = timedelta(minutes=5)

# --- Config entry keys ---
CONF_DEVICE_ID = "device_id"
CONF_AREA_CODE = "area_code"

# --- Coordinator ---
DEFAULT_UPDATE_INTERVAL = timedelta(seconds=60)
