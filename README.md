# Meowant

A Home Assistant custom integration for the Meowant self-cleaning litter box, built against Meowant's cloud API. See [the integration's README](custom_components/meowant/README.md) for installation.

**Status:** early. Login and the device list work (see [`API.md`](API.md)), but the integration doesn't create any entities yet.

## Disclaimer

This is an unofficial client for personal use with an account and device the author owns. It is not affiliated with, endorsed by, or supported by Meowant. The upstream API is undocumented and can change without notice.

## Development

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest tests/
```

Tests need no network and no Home Assistant install (see `tests/conftest.py`).

### Dev console

A small local page for poking at the cloud API before it's wired into Home Assistant. Copy `.env.example` to `.env` and fill in your email and password (and country code, if not `US`), then:

```
.venv\Scripts\python.exe -m uvicorn backend.app:app --reload --port 8765
```

Open `http://localhost:8765` and log in once. The server keeps that session, so reloading the page doesn't log in again. From there:

- **State:** the box's settings, waste-bin counter, deodorant, online status, recent cat visits and device events, and cat profiles, shown as raw values. Refresh manually or on a timer.
- **Actions:** reset the waste-bin counter, deodorant alert and "replaced", auto clean, and generic forms for feature switches and time windows.
- **Request console:** send a signed, authenticated request to any path. `{serial}` fills in the selected box. Save a request as a custom button; buttons are stored in your browser.

Tokens, emails and passwords are redacted from everything the page shows. The fake client install ID is persisted to `.dev_device_id` (gitignored).

## Repo layout

```
custom_components/meowant/   # Home Assistant integration
backend/ + frontend/         # local dev console (FastAPI + static HTML)
tests/                       # unit tests for api.py's pure logic
_meowant_dev_import.py       # lets backend/ and tests/ reuse api.py without installing homeassistant
API.md                       # cloud API reference
CHANGELOG.md
```

## License

[MIT](LICENSE)
