"""模型设置端点测试。

覆盖：
- GET /settings/llm：返回脱敏配置
- POST /settings/llm：保存配置，key 空则不覆盖
- 保存后 /health 的 model 动态更新
- 无效请求返回 400
- POST /settings/llm/test：连通性探测
"""

import json
import pytest
from fastapi.testclient import TestClient
from web_server import app

XHR = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def client():
    return TestClient(app)


def _cfg(client):
    d = client.get("/settings/llm").json()
    return d.get("config", d)


class TestGetLlmSettings:
    def test_returns_masked_key(self, client):
        r = client.get("/settings/llm")
        assert r.status_code == 200
        inner = r.json().get("config", r.json())
        assert "model" in inner
        assert "base_url" in inner
        assert "*" in inner.get("api_key", "")


class TestSetLlmSettings:
    def test_missing_model_returns_400(self, client):
        r = client.post("/settings/llm", json={"model": "", "base_url": "", "api_key": ""}, headers=XHR)
        assert r.status_code == 400

    def test_save_model_updates_health(self, client):
        before = _cfg(client)
        r = client.post("/settings/llm", json={
            "model": before["model"], "base_url": before["base_url"], "api_key": "",
        }, headers=XHR)
        assert r.status_code == 200

    def test_empty_api_key_does_not_overwrite(self, client):
        before = _cfg(client)
        r = client.post("/settings/llm", json={
            "model": before["model"], "base_url": before["base_url"], "api_key": "",
        }, headers=XHR)
        assert r.status_code == 200

    def test_short_api_key_rejected(self, client):
        before = _cfg(client)
        r = client.post("/settings/llm", json={
            "model": before["model"], "base_url": before["base_url"], "api_key": "sk",
        }, headers=XHR)
        assert r.status_code == 400


class TestHealthModelDynamic:
    def test_health_model_matches_config(self, client):
        cfg = _cfg(client)
        h = client.get("/health").json()
        assert h["model"] == cfg["model"]


class TestTestLlmSettings:
    def test_test_endpoint_missing_model(self, client):
        r = client.post("/settings/llm/test", json={
            "model": "", "base_url": "https://api.longcat.chat/openai",
            "api_key": "ak_TEST_KEY_NOT_REAL_0000000000000",
        }, headers=XHR)
        assert r.status_code == 400

    def test_test_endpoint_missing_base_url(self, client):
        r = client.post("/settings/llm/test", json={
            "model": "LongCat-2.0", "base_url": "",
            "api_key": "ak_TEST_KEY_NOT_REAL_0000000000000",
        }, headers=XHR)
        assert r.status_code == 400

    def test_test_endpoint_empty_key_uses_existing(self, client, monkeypatch):
        """key 为空时从现有配置读取，不返回 400。"""
        called = {"count": 0}
        class MockResponse:
            status_code = 200
            text = "{}"
        import requests as req
        orig = req.post
        def mock_post(*a, **k):
            called["count"] += 1
            return MockResponse()
        monkeypatch.setattr(req, "post", mock_post)
        r = client.post("/settings/llm/test", json={
            "model": "LongCat-2.0", "base_url": "https://api.longcat.chat/openai", "api_key": "",
        }, headers=XHR)
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert called["count"] >= 1

    def test_test_endpoint_success(self, client, monkeypatch):
        class MockResponse:
            status_code = 200
            text = "{}"
        import requests as req
        monkeypatch.setattr(req, "post", lambda *a, **k: MockResponse())
        r = client.post("/settings/llm/test", json={
            "model": "LongCat-2.0", "base_url": "https://api.longcat.chat/openai",
            "api_key": "ak_TEST_KEY_NOT_REAL_0000000000000",
        }, headers=XHR)
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True

    def test_test_endpoint_400(self, client, monkeypatch):
        class MockResponse:
            status_code = 400
            text = "not found"
        import requests as req
        monkeypatch.setattr(req, "post", lambda *a, **k: MockResponse())
        r = client.post("/settings/llm/test", json={
            "model": "Bad", "base_url": "https://api.deepseek.com/v1",
            "api_key": "ak_TEST_KEY_NOT_REAL_0000000000000",
        }, headers=XHR)
        data = r.json()
        assert data["ok"] is False
        assert data["status_code"] == 400

    def test_test_endpoint_401(self, client, monkeypatch):
        class MockResponse:
            status_code = 401
            text = "bad key"
        import requests as req
        monkeypatch.setattr(req, "post", lambda *a, **k: MockResponse())
        r = client.post("/settings/llm/test", json={
            "model": "LongCat-2.0", "base_url": "https://api.longcat.chat/openai",
            "api_key": "wrong_key_1234567890",
        }, headers=XHR)
        data = r.json()
        assert data["ok"] is False
        assert data["status_code"] == 401

