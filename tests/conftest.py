from __future__ import annotations

import os
from pathlib import Path

import pytest

from job_radar.config import Config
from job_radar.db import connect, migrate


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    private = tmp_path / "private"
    private.mkdir()
    monkeypatch.setenv("JOB_RADAR_PRIVATE", str(private))
    c = Config.load(tmp_path)
    c.ensure_dirs()
    return c


@pytest.fixture
def conn(cfg):
    c = connect(cfg)
    migrate(c)
    yield c
    c.close()
