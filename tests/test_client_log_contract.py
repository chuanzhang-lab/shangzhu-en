"""/client-log 上报端点契约（E-06）：防滥用四闸 + WEBCLIENT 落盘痕。

验收口径（计划 E-06）：curl 超限 → 400；非 JSON → 400；正常 → 日志出现
WEBCLIENT 行；字段白名单 + 截断生效（超长 stage/code/message 被削到定长）。
节流 60s 在前端 reportClientError（e2e 探针验证），端点本身不节流——
上报端点绝不给前端制造二次错误：合法形状恒回 {"ok": true}。
"""
import json
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

from fastapi.testclient import TestClient  # noqa: E402

os.environ.pop("PGDATABASE_URL", None)


def _client():
    import web_server as ws
    orig = ws.get_store
    ws.get_store = lambda: type("S", (), {"close": lambda: None})()
    try:
        return TestClient(ws.app)
    finally:
        ws.get_store = orig


def test_payload_too_large_400():
    """超 2KB 直接拒——上报通道不许变成大文本注入通道。"""
    r = _client().post("/client-log", content=b"x" * 2049)
    assert r.status_code == 400, f"超限未拒: {r.status_code}"
    assert "too large" in r.json().get("error", "")


def test_non_json_400():
    """非 JSON / 非对象 body 都拒；sendBeacon 兜底的 fetch 会带 JSON，形状不符即攻击或 bug。"""
    c = _client()
    assert c.post("/client-log", content=b"not json").status_code == 400
    assert c.post("/client-log", content=b"[1,2,3]").status_code == 400


def test_normal_post_logs_webclient(caplog):
    """正常上报恒 {"ok": true}，且日志必须留下 WEBCLIENT 痕（降级必有痕）。"""
    caplog.set_level(logging.INFO, logger="web")
    body = {"stage": "loadTasks", "code": "TASKS_LOAD_FAILED", "message": "Failed to fetch",
            "page_ver": "1700000000", "ts": "2026-10-04T00:00:00Z"}
    r = _client().post("/client-log", json=body)
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert "WEBCLIENT" in caplog.text, f"日志无 WEBCLIENT 行：{caplog.text!r}"
    assert "loadTasks" in caplog.text and "TASKS_LOAD_FAILED" in caplog.text


def test_field_whitelist_truncation(caplog):
    """超长字段按白名单定长截断（stage/code 40、message 500、page_ver 20、ts 40）。

    字段取「超各自上限、但总 body < 2KB」的尺寸：过截断闸，不过 2KB 闸，
    两道闸才能分别验证。
    """
    caplog.set_level(logging.INFO, logger="web")
    body = {"stage": "s" * 100, "code": "c" * 100, "message": "m" * 600,
            "page_ver": "v" * 30, "ts": "t" * 50, "extra": "ignored"}
    r = _client().post("/client-log", json=body)
    assert r.status_code == 200
    line = [ln for ln in caplog.text.splitlines() if "WEBCLIENT" in ln][-1]
    assert "s" * 40 in line and "s" * 41 not in line, f"stage 未截到 40: {line!r}"
    assert "c" * 40 in line and "c" * 41 not in line, f"code 未截到 40: {line!r}"
    assert "m" * 500 in line and "m" * 501 not in line, f"message 未截到 500: {line!r}"
    assert "v" * 20 in line and "v" * 21 not in line, f"page_ver 未截到 20: {line!r}"
    assert "ignored" not in line, "白名单外字段不该落进日志"


def test_csrf_guard_still_blocks_other_writes():
    """/client-log 豁免不得顺手放开其他写接口（豁免面最小化）。"""
    r = _client().post("/tasks", json={"name": "guard"})
    assert r.status_code == 403, f"其他写接口的 X-Requested-With 防护被豁免波及: {r.status_code}"
