"""Unit tests for custom_components/meowant/api.py's pure logic."""
from __future__ import annotations

from custom_components.meowant.api import MeowantApiError, MeowantAuthError


def test_auth_error_is_api_error():
    # The coordinator and config flow rely on catching MeowantAuthError before
    # the broader MeowantApiError.
    assert issubclass(MeowantAuthError, MeowantApiError)
