# Meowant

A Home Assistant custom integration for the Meowant self-cleaning litter box, built against Meowant's cloud API. See [the integration's README](custom_components/meowant/README.md) for installation.

**Status:** early scaffolding. The upstream API hasn't been reverse-engineered yet (see [`API.md`](API.md)), so the integration doesn't create any entities.

## Disclaimer

This is an unofficial, reverse-engineered client for personal/interoperability use against an account and device the author owns. It is not affiliated with, endorsed by, or supported by Meowant. The upstream API is undocumented and can change without notice.

## Development

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest tests/
```

Copy `.env.example` to `.env` for local dev scripts. Tests need no network and no Home Assistant install (see `tests/conftest.py`).

## Repo layout

```
custom_components/meowant/   # Home Assistant integration
tests/                       # unit tests for api.py's pure logic
_meowant_dev_import.py       # lets tests/ reuse api.py without installing homeassistant
API.md                       # reverse-engineered API reference
CHANGELOG.md
```

## License

[MIT](LICENSE)
