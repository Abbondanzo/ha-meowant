# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/). Version numbers track `custom_components/meowant/manifest.json`.

## [0.0.2] - 2026-10-21

### Added
- Real login against the Meowant (UBTech AirPet) cloud, matching the AIR PET Android app: `X-UBT-Sign` signing with millisecond timestamps, MD5 password hashing, numeric `accountType`, `areaCode` country routing, token expiry handling, and one re-login on HTTP 401. Verified against a live account. See `API.md`.
- Country code field in the config flow (default `US`). Values that aren't two-letter ISO codes are rejected before contacting the server.
- Device list fetch; coordinator data is keyed by serial number.
- A per-config-entry client device ID (`X-UBT-DeviceId`), generated at setup.
- Distinct handling for the server's temporary lockout (code 2342) after repeated failed logins. Each login makes a single attempt so it doesn't add to the lockout.
- Bundled Meowant tenant constants (base URL, app ID, app key, product) in `const.py`.
- Local dev console (`backend/` + `frontend/`): login, device state (overview, activity, cats, settings), actions, and a signed request console.
- API client reads and writes for box config, waste bin, deodorant, online status, event records, cats, feature switches and time windows.

## [0.0.1] - 2026-10-21

### Added
- Project scaffolding: config flow (email/password) with reauth, polling coordinator, and an API client stub. No entities yet.
