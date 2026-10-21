# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/). Version numbers track `custom_components/meowant/manifest.json`.

## [0.1.0] - 2026-10-21

First version with entities. Everything is read-only; nothing writes to the box.

### Added
- Litter box sensors: waste bin level and uses, deodorant remaining, last cat visit (with cat, weight and duration), last visit weight and duration, last clean, last device event and auto-clean delay (diagnostic).
- Binary sensors: online, waste bin needs emptying, deodorant expired.
- Activity event entity that fires for each new cat visit, scheduled clean or clean after a visit, for automations.
- One device per cat with weight, visits today and time in box today. Cats added in the app later are picked up automatically.
- Diagnostics download with personal data redacted.
- Logged-in requests send `X-UBT-Language: en`, so cat breeds come back in English.

## [0.0.3] - 2026-10-21

### Added
- Integration icon and logo, served locally from `custom_components/meowant/brand/`.

### Changed
- The config flow's country field is now Home Assistant's country picker, pre-filled with United States. Phone prefixes like `1` can no longer be entered.
- The setup form keeps the email and country you entered when it's shown again after an error.

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
