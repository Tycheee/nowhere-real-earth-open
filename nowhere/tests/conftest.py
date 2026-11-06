# -*- coding: utf-8 -*-
"""Shared fixtures for nowhere-real-earth tests.

- Redirects all persistence (state saves, geocode disk cache) to a tmp dir
  via NOWHERE_HOME so tests never touch the user's home directory.
- Resets geocode caches between tests.
"""
import pathlib

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "nowhere_home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NOWHERE_HOME", str(home))
    monkeypatch.setenv("NOWHERE_QWEATHER_KEY", "")
    from nowhere import geocode
    geocode._CACHE_PATH = geocode.cache_dir() / "geocode_cache.json"
    geocode.clear_cache()
    geocode.reset_disk_cache()
    yield home


@pytest.fixture
def reset_coast():
    """Reset the coast module cache so mask-path monkeypatching takes effect."""
    from nowhere import coast
    coast._MASK = None
    yield
    coast._MASK = None
