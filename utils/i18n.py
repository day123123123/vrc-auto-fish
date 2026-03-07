"""
Internationalization (i18n) utility
====================================
Provides t(key, **kwargs) for locale-aware string lookup.
Language is set via config.LANGUAGE ("en" / "zh" / "ja").
"""

import json
import pathlib

_cache: dict = {}


def _load(lang: str) -> dict:
    path = pathlib.Path(__file__).parent.parent / "locales" / f"{lang}.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def t(key: str, **kwargs) -> str:
    """Look up a locale string by dot-notation key, e.g. t('gui.btn_start')."""
    global _cache
    if not _cache:
        try:
            import config
            lang = getattr(config, "LANGUAGE", "en")
        except Exception:
            lang = "en"
        try:
            _cache = _load(lang)
        except FileNotFoundError:
            _cache = _load("en")
    node = _cache
    for part in key.split("."):
        node = node[part]
    return node.format(**kwargs) if kwargs else node


def reset_cache():
    """Clear the locale cache (e.g. after language change)."""
    global _cache
    _cache = {}
