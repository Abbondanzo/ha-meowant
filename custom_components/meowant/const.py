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

# Sent as X-UBT-Language on logged-in requests (not login), like the app.
# The server localizes some fields by it, e.g. cat breed names (Chinese otherwise).
API_LANGUAGE = "en"

# Matches the AIR PET app.
LOGIN_METHOD = "PUT"

# accountType values the login endpoint accepts (JSON numbers); tried in order.
ACCOUNT_TYPE_EMAIL = 1
ACCOUNT_TYPE_PHONE = 0

# Login areaCode: the account's ISO country code, chosen at signup in the app.
DEFAULT_AREA_CODE = "US"

# Re-login this long before the token's stated expiry.
TOKEN_REFRESH_MARGIN = timedelta(minutes=5)

# --- IM channel (MQTT over TLS on the base URL's host; see API.md) ---
MQTT_PORT = 21000
MQTT_KEEPALIVE = 20  # seconds, as the app sends it

# Live state: the box pushes every change; this all-state poll is a backstop.
LIVE_POLL_INTERVAL = timedelta(seconds=60)
# A box that hasn't answered for this long shows as unavailable.
LIVE_STATE_MAX_AGE = timedelta(minutes=3)
# Reconnect backoff. Each reconnect fetches fresh MQTT credentials over REST.
LIVE_RETRY_MIN = timedelta(seconds=30)
LIVE_RETRY_MAX = timedelta(minutes=15)

# --- Config entry keys ---
CONF_DEVICE_ID = "device_id"
CONF_AREA_CODE = "area_code"

# --- Coordinator ---
DEFAULT_UPDATE_INTERVAL = timedelta(seconds=60)

# Records fetched per box per poll (each of cat visits and device events).
# Enough that a busy minute between polls doesn't drop events.
RECORDS_PER_POLL = 10
