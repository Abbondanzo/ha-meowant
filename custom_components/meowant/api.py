"""Client for the Meowant cloud API.

Deliberately has no homeassistant dependency -- only aiohttp and const.py -- so
tests/ can import it without installing homeassistant. See _meowant_dev_import.py.
"""
from __future__ import annotations

from typing import Any

import aiohttp

from .const import API_BASE_URL


class MeowantApiError(Exception):
    """Any failure talking to the Meowant API."""


class MeowantAuthError(MeowantApiError):
    """Credentials were rejected."""


class MeowantApiClient:
    """Thin async client for the Meowant cloud API."""

    def __init__(self, session: aiohttp.ClientSession, email: str, password: str) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._base_url = API_BASE_URL

    async def login(self) -> None:
        """Authenticate. Raises MeowantAuthError on bad credentials."""
        raise NotImplementedError("Meowant login flow not yet reverse-engineered; see API.md")

    async def get_devices(self) -> dict[str, Any]:
        """Return current state for every litter box on the account, keyed by device ID."""
        raise NotImplementedError("Meowant device endpoint not yet reverse-engineered; see API.md")
