# CLAUDE.md

Guidance for working in this repo.

## What this is

A Home Assistant custom integration for the Meowant self-cleaning litter box, built against Meowant's (undocumented) cloud API. The deliverable is `custom_components/meowant/`.

The upstream protocol is being reverse-engineered; everything learned about it (endpoints, auth flow, quirks) goes in `API.md`. Read it before touching anything that talks to the upstream API.

## Architecture notes

- `custom_components/meowant/api.py` and `const.py` have **no `homeassistant` dependency**: only `aiohttp` and each other. Every other file in that package (`__init__.py`, `coordinator.py`, `config_flow.py`, entity platforms) depends on `homeassistant`. This split is deliberate: it lets `tests/` and ad-hoc dev scripts reuse the API client without installing the (large) `homeassistant` package.
- `_meowant_dev_import.py` (repo root) is how that reuse works: it registers stub parent-package entries in `sys.modules` and loads `api.py`/`const.py` by file path, bypassing the real `__init__.py`. If you rename or restructure `custom_components/meowant/`, this loader needs to follow.
- `manifest.json`'s `requirements` is intentionally `[]`. The integration should need nothing beyond `aiohttp`, which Home Assistant core already ships. Don't add a requirements entry without a real reason.

## Working against the live API

- **Don't blind-guess endpoints/params against production.** Capture real app traffic (HTTP Toolkit + a rooted/Google-APIs Android emulator) to resolve unknown endpoints. Guessing routes cost a lot of wasted effort on the sibling `ha-hercules` project.
- **Never print credentials or session tokens, even in debug/trace output.** If you add debug logging around `api.py`, redact by scanning recursively for known-sensitive key names (`token`, `password`, `phone`, `email`, ...), not by hardcoding an assumed JSON path.

## Running things

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest tests/
```

There's no way to fully test the integration outside a real Home Assistant instance (config flow rendering, entity registration). What *can* be verified locally: `py_compile` on every `.py` file, `json.load` on `manifest.json`/`strings.json`/`translations/en.json`, and `pytest tests/` for the pure logic in `api.py`.

## Releasing changes to the HA integration

**Every change to `custom_components/meowant/` gets its own `CHANGELOG.md` entry and a version bump in `manifest.json`.** HACS detects updates by `manifest.json`'s `version` changing. Add a new `## [x.y.z] - YYYY-MM-DD` section at the top of `CHANGELOG.md` and keep the two in sync.

`strings.json` and `translations/en.json` must stay identical; edit both.

## Credentials

`.env` (gitignored) holds `MEOWANT_EMAIL`/`MEOWANT_PASSWORD` for local dev scripts. The HA integration stores credentials in HA's config entry storage via `config_flow.py`; it has no `.env` of its own.
