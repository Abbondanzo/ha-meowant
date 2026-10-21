"""Dev-only loader for reusing custom_components/meowant/api.py and const.py
from outside Home Assistant (in tests/ and ad-hoc scripts), without requiring
the `homeassistant` package to be installed.

Why this exists: custom_components/meowant/__init__.py imports real
`homeassistant` modules, as any real HA integration's entry point must. A plain
`import custom_components.meowant.api` would execute that __init__.py first
(Python always imports parent packages before submodules) and fail here, since
this repo intentionally does NOT install the (large) homeassistant package.

api.py, const.py and models.py have no homeassistant dependency at all -- only
aiohttp and each other -- so this loader registers stub parent-package entries
directly in sys.modules and loads those files by path, skipping the real
__init__.py entirely.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
_PKG_DIR = _REPO_ROOT / "custom_components" / "meowant"


def _load_by_path(fullname: str, filename: str) -> types.ModuleType:
    if fullname in sys.modules:
        return sys.modules[fullname]
    spec = importlib.util.spec_from_file_location(fullname, _PKG_DIR / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[fullname] = module
    spec.loader.exec_module(module)
    return module


def load_meowant_api() -> types.ModuleType:
    """Import and return custom_components.meowant.api, bypassing that
    package's homeassistant-dependent __init__.py."""
    for name, path in (
        ("custom_components", _REPO_ROOT / "custom_components"),
        ("custom_components.meowant", _PKG_DIR),
    ):
        if name not in sys.modules:
            stub = types.ModuleType(name)
            stub.__path__ = [str(path)]
            sys.modules[name] = stub

    _load_by_path("custom_components.meowant.const", "const.py")
    _load_by_path("custom_components.meowant.models", "models.py")
    return _load_by_path("custom_components.meowant.api", "api.py")
