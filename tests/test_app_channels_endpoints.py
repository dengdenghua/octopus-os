from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runtime.platform.ui.app import create_app


def test_create_app_exposes_channels_without_runtime_manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    client = TestClient(create_app(channel_manager=None))

    response = client.get("/api/channels")

    assert response.status_code == 200
    data = response.json()
    platforms = {row["platform"] for row in data}
    assert {"wechat", "dingtalk", "feishu", "telegram", "slack"} <= platforms
    assert all(row["connected"] is False for row in data)
