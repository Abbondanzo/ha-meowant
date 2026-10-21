# CLAUDE.md

Guidance for working in this repo.

## What this is

A Home Assistant custom integration for the Meowant self-cleaning litter box, built against Meowant's (undocumented) cloud API, which is UBTech's "AirPet" platform. The deliverable is `custom_components/meowant/`. `backend/` + `frontend/` is a local dev console (login, device state, actions, request console) that shares the same `api.py` client. Don't put API logic in `backend/app.py`.

The upstream API is undocumented; everything learned about it (endpoints, auth flow, quirks) goes in `API.md`. Read it before touching anything that talks to the upstream API.

## Architecture notes

- `custom_components/meowant/api.py` and `const.py` have **no `homeassistant` dependency**: only `aiohttp` and each other. Every other file in that package (`__init__.py`, `coordinator.py`, `config_flow.py`, entity platforms) depends on `homeassistant`. This split is deliberate: it lets `backend/`, `tests/` and ad-hoc dev scripts reuse the API client without installing the (large) `homeassistant` package.
- `_meowant_dev_import.py` (repo root) is how that reuse works: it registers stub parent-package entries in `sys.modules` and loads `api.py`/`const.py` by file path, bypassing the real `__init__.py`. If you rename or restructure `custom_components/meowant/`, this loader needs to follow.
- Each login sends exactly one attempt. The server locks the account (code 2342) after repeated failures, so never loop over account types or retry logins automatically.
- `manifest.json`'s `requirements` is intentionally `[]`. The integration should need nothing beyond `aiohttp`, which Home Assistant core already ships. Don't add a requirements entry without a real reason.

## Working against the live API

- [CrazzyBerg/upet-hass](https://github.com/CrazzyBerg/upet-hass) implements the same protocol for the UPET brand, but it has **no license**. Use it as a protocol reference only; don't copy its code.
- **Don't blind-guess endpoints/params against production.** Confirm a request's exact shape against what the official app sends before implementing it. Guessing routes cost a lot of wasted effort on the sibling `ha-hercules` project, and here failed logins lock the account.
- **Never print credentials or session tokens, even in debug/trace output.** If you add debug logging around `api.py`, redact by scanning recursively for known-sensitive key names (`token`, `password`, `phone`, `email`, ...), not by hardcoding an assumed JSON path.

## Running things

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest tests/
# dev console at http://localhost:8765
.venv\Scripts\python.exe -m uvicorn backend.app:app --reload --port 8765
```

There's no way to fully test the integration outside a real Home Assistant instance (config flow rendering, entity registration). What *can* be verified locally: `py_compile` on every `.py` file, `json.load` on `manifest.json`/`strings.json`/`translations/en.json`, and `pytest tests/` for the pure logic in `api.py`.

## Releasing changes to the HA integration

**Every change to `custom_components/meowant/` gets its own `CHANGELOG.md` entry and a version bump in `manifest.json`.** HACS detects updates by `manifest.json`'s `version` changing. Add a new `## [x.y.z] - YYYY-MM-DD` section at the top of `CHANGELOG.md` and keep the two in sync.

`strings.json` and `translations/en.json` must stay identical; edit both.

## Credentials

`.env` (gitignored) holds `MEOWANT_EMAIL`/`MEOWANT_PASSWORD`/`MEOWANT_AREA_CODE` for the dev console. `MEOWANT_AREA_CODE` is the two-letter ISO country code (`US`), not the phone prefix; the wrong value gives error 2004 and burns login attempts toward a lockout.

The tenant constants in `const.py` (`DEFAULT_BASE_URL`/`DEFAULT_APP_ID`/`DEFAULT_APP_KEY`/`DEFAULT_PRODUCT`) are static app-level values shipped in every copy of the AIR PET app, not per-user secrets. Don't treat them as sensitive, but also don't confuse them with the user's actual email/password. The HA integration stores credentials in HA's config entry storage via `config_flow.py`; it has no `.env` of its own.
