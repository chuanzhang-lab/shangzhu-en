"""web_server HTTP 层健壮性测试：验证服务在各种边界输入下稳定返回
（不 500、不卡死），并验证 /health、多轮会话累积等行为。

固定 llm_advise 为桩（不真实联网），聚焦 HTTP/路由/解析层稳定性。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# web_server 导入时会 os.chdir 到 src/，导入完成后立即恢复仓库根 cwd，
# 避免影响 run_all 后续模块（web_server 运行不依赖 cwd，全用绝对路径）。
_saved = os.getcwd()
import web_server as ws
os.chdir(_saved)

from fastapi.testclient import TestClient

# 桩掉 LLM 调用（不真实联网），专注 HTTP/路由/解析层
ws.llm_advise = lambda scan, user_text="", session_snapshot=None, context=None: {"text": "", "ops": []}
_client = TestClient(ws.app)
_XHR = {"X-Requested-With": "XMLHttpRequest"}


def _chat(messages, tid="rb"):
    return _client.post("/chat", json={"messages": messages, "thread_id": tid}, headers=_XHR)


def test_health_ok():
    r = _client.get("/health")
    assert r.status_code == 200
    assert "model" in r.json()


def test_empty_messages_400():
    # 空 messages 数组 → 无用户消息 → 400
    r = _chat([])
    assert r.status_code == 400


def test_only_assistant_400():
    r = _chat([{"role": "assistant", "content": "hi"}])
    assert r.status_code == 400


def test_missing_messages_field_422():
    # 缺 messages 字段 → Pydantic 校验 422（框架层，防止未来模型破坏致 500）
    r = _client.post("/chat", json={"thread_id": "x"}, headers=_XHR)
    assert r.status_code == 422


def test_normal_structured_200():
    r = _chat([{"role": "user",
                "content": "奶茶店月租金1万员工2人工资各5000客单价15日售50杯"}])
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "structured"
    assert body["intent"] in ("quick_scan", "trend", "compare", "suggest")


def test_chitchat_returns_steward():
    r = _chat([{"role": "user", "content": "你好，随便聊聊"}], tid="rb-chat")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "steward"
    assert body["intent"] == "chitchat"


def test_very_long_input_no_crash():
    # 超长输入不应导致崩溃或卡死（to_thread 不冻结事件循环）
    long_text = "开奶茶店 " * 4000  # ~12000 字
    r = _chat([{"role": "user", "content": long_text}], tid="rb-long")
    assert r.status_code in (200, 400)


def test_session_accumulates_across_turns():
    # 多轮累积：第一轮给部分参数，第二轮补参，两轮都应稳定 200
    tid = "rb-acc"
    r1 = _chat([{"role": "user", "content": "开奶茶店，月租金1万"}], tid=tid)
    assert r1.status_code == 200
    r2 = _chat([{"role": "user", "content": "员工2人工资各5000"}], tid=tid)
    assert r2.status_code == 200


def test_unknown_intent_falls_to_steward():
    # 无法归类的业务/闲聊文本应走 steward，而非 500
    r = _chat([{"role": "user", "content": "今天天气不错，顺便问下我这店能赚钱吗"}],
              tid="rb-unk")
    assert r.status_code == 200
    assert r.json()["mode"] == "steward"


def test_compare_routes_and_uses_alt_params():
    # 对比意图应调用 compare_scenarios，并解析「如果」后的参数作为方案 B
    tid = "rb-compare"
    r1 = _chat([{"role": "user",
                  "content": "奶茶店月租金1万员工2人工资各5000客单价15日售50杯"}], tid=tid)
    assert r1.status_code == 200
    r2 = _chat([{"role": "user",
                  "content": "如果日售提高到80杯，对比一下"}], tid=tid)
    assert r2.status_code == 200
    body = r2.json()
    assert body["mode"] == "structured"
    assert body["intent"] == "compare"


def test_benchmark_and_market_routed_200():
    # 行业基准与市场调研意图都应稳定返回结构化数据，不 500
    for tid, text in [("rb-bench", "查一下奶茶行业毛利率基准"),
                      ("rb-market", "2025年奶茶市场规模")]:
        r = _chat([{"role": "user", "content": text}], tid=tid)
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "structured"
        assert body["intent"] in ("benchmark", "market")


def test_report_excel_routed_200():
    # 报告意图应调用报告生成工具并返回下载链接/路径
    tid = "rb-report-excel"
    r = _chat([{"role": "user",
                  "content": "奶茶店月租金1万员工2人工资各5000客单价15日售50杯，导出Excel"}],
              tid=tid)
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "structured"
    assert body["intent"] == "report_excel"
    # 本地生成路径会在 Markdown 内容里包含本地文件路径
    assert "报告已生成" in body["content"] or "file://" in body["content"]


def test_health_includes_version_and_uptime():
    r = _client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "model" in body
    assert "version" in body
    assert body["version"] == ws.APP_VERSION
    assert "uptime_seconds" in body
    assert isinstance(body["uptime_seconds"], (int, float))
    assert "llm_configured" in body
    assert isinstance(body["llm_configured"], bool)
    assert "sessions" in body
    assert "active_sessions" in body["sessions"]


def test_oversized_input_returns_400():
    # 超过 _MAX_INPUT_LENGTH 字符应返回 400，不触发后续工具/LLM
    long_text = "开奶茶店 " * 5000
    r = _chat([{"role": "user", "content": long_text}], tid="rb-oversize")
    assert r.status_code == 400
    assert "过长" in r.json()["error"]


def test_session_params_not_mutated_by_route():
    # 下游 setdefault 不得写回 SessionState（merged 必须是拷贝）
    tid = "rb-noleak"
    r1 = _chat([{"role": "user", "content": "开奶茶店，月租金1万"}], tid=tid)
    assert r1.status_code == 200
    before = dict(ws.get_state(tid).get("params") or {})
    r2 = _chat([{"role": "user", "content": "你好"}], tid=tid)
    assert r2.status_code == 200
    after = dict(ws.get_state(tid).get("params") or {})
    # chitchat 不改 params；且不应凭空多出字段
    assert after == before


def test_concurrent_chats_no_crash():
    # 并发请求不应 500 / 卡死（工具与 LLM 均在 to_thread）
    import concurrent.futures

    def one(i):
        return _chat(
            [{"role": "user", "content": f"奶茶店月租金{10000 + i}员工2人工资各5000客单价15日售50杯"}],
            tid=f"rb-conc-{i}",
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(one, range(6)))
    assert all(r.status_code == 200 for r in results)
    assert all(r.json().get("mode") == "structured" for r in results)


def test_structured_chat_does_not_auto_advise():
    """/chat structured 立即返回规则结果，不附加 AI 解读，并给出 idle 按需入口。"""
    r = _chat([{"role": "user",
                "content": "奶茶店月租金1万员工2人工资各5000客单价15日售50杯"}],
              tid="rb-no-auto-advise")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "structured"
    assert "AI 解读" not in body["content"]
    advice = body.get("ai_advice") or {}
    assert advice.get("available") is True
    assert advice.get("status") == "idle"
    assert advice.get("analysis_id")
    assert isinstance(advice.get("params_version"), int)


def test_analysis_advice_success_timeout_and_stale():
    """POST /analysis/advice：成功返回文本；超时标 timeout；参数变更后标 stale。"""
    tid = "rb-advice"
    r = _chat([{"role": "user",
                "content": "奶茶店月租金1万员工2人工资各5000客单价15日售50杯"}],
              tid=tid)
    assert r.status_code == 200
    meta = r.json()["ai_advice"]

    saved = ws.llm_advise
    try:
        ws.llm_advise = lambda scan, user_text="", session_snapshot=None, context=None: {
            "text": "本轮利润由引擎给出，解读只翻译数字。",
            "ops": [],
        }
        ok = _client.post("/analysis/advice", json={
            "thread_id": tid,
            "analysis_id": meta["analysis_id"],
            "params_version": meta["params_version"],
        }, headers=_XHR)
        assert ok.status_code == 200
        ok_body = ok.json()
        assert ok_body["status"] == "ok"
        assert "利润" in ok_body["text"]

        async def _timeout(*args, timeout=None, **kwargs):
            return {"text": "", "ops": [], "_skipped": "timeout"}

        saved_wait = ws._advise_with_timeout
        try:
            ws._advise_with_timeout = _timeout
            timed = _client.post("/analysis/advice", json={
                "thread_id": tid,
                "analysis_id": meta["analysis_id"],
                "params_version": meta["params_version"],
            }, headers=_XHR)
            assert timed.status_code == 200
            assert timed.json()["status"] == "timeout"
        finally:
            ws._advise_with_timeout = saved_wait
    finally:
        ws.llm_advise = saved

    r2 = _chat([{"role": "user",
                 "content": "奶茶店月租金1.2万员工3人工资各5000客单价15日售50杯"}],
               tid=tid)
    assert r2.status_code == 200

    # 参数变更后，旧 analysis_id 的 advice 必须判 stale（不得返回旧解读）
    stale = _client.post("/analysis/advice", json={
        "thread_id": tid,
        "analysis_id": meta["analysis_id"],
        "params_version": meta["params_version"],
    }, headers=_XHR)
    assert stale.status_code == 200
    assert stale.json()["status"] == "stale"
    assert not stale.json().get("text")


# ── 敏感度分析：展示串 vs 数值 的等价性守护（原崩溃区，零覆盖区）────────────
# 背景：quick_scan 出口把 variable_cost_ratio 渲染成 "60%"，消费方拿去做算术会抛
# TypeError；同函数内 DAYS_PER_MONTH 的延迟导入位置又会引发 UnboundLocalError。
# 这两个缺陷曾让 sensitivity 意图 100% 返回 fallback 文案（崩溃被伪装成业务兜底）。

_BASE = {
    "monthly_rent": 10000.0, "daily_traffic": 50.0, "price_per_unit": 15.0,
    "variable_cost_ratio": 0.6, "monthly_fixed_cost": 20000.0,
    "monthly_revenue": 22500.0, "avg_salary": 5000.0, "employee_count": 2.0,
}
_VARS = ["daily_traffic", "monthly_rent", "variable_cost_ratio",
         "price_per_unit", "employee_count"]


def test_as_ratio_normalizes_all_forms():
    """'60%' / 60 / 0.6 / '0.6' 都必须归一到 0~1；不可解析返回 None。"""
    for raw, exp in [("60%", 0.6), (60, 0.6), ("0.6", 0.6), (0.6, 0.6)]:
        got = ws._as_ratio(raw)
        assert got is not None and abs(got - exp) < 1e-9, f"{raw!r} -> {got}"
    for bad in [None, "abc", "％", True, object()]:
        assert ws._as_ratio(bad) is None, f"{bad!r} 应返回 None"


def test_single_var_sensitivity_equal_for_str_and_float():
    """同一种业务含义，展示串输入必须与数值输入得到完全一致的结果。"""
    for var in _VARS:
        with_float = ws._build_single_variable_sensitivity(dict(_BASE), var)
        with_str = ws._build_single_variable_sensitivity(
            {**_BASE, "variable_cost_ratio": "60%"}, var)
        assert with_float.get("breakeven_value") == pytest.approx(
            with_str.get("breakeven_value")), f"{var}: 展示串与数值结果不一致"


def test_single_var_sensitivity_daily_traffic_breakeven():
    """DAYS_PER_MONTH 前置引用不得再抛 UnboundLocalError。

    保本客流 = 20000 / (15 × 30 × 0.4) ≈ 111.1
    """
    r = ws._build_single_variable_sensitivity(dict(_BASE), "daily_traffic")
    assert r["breakeven_value"] == pytest.approx(111.1, abs=0.1), r
    assert len(r["sensitivity_curve"]) == 11
    assert r["interpretation"]


def test_sensitivity_intent_returns_analysis_not_fallback():
    """端到端：sensitivity 意图不得再退化为「无法生成结构化分析」兜底文案。"""
    tid = "rb-sensitivity-reg"
    seed = _chat([{"role": "user",
                   "content": "开奶茶店，月租金1万，日售50杯，单价15，变动成本率60%，员工2人工资各5000"}],
                 tid=tid)
    assert seed.status_code == 200

    r = _chat([{"role": "user", "content": "做个敏感度分析"}], tid=tid)
    assert r.status_code == 200
    body = r.json()
    assert "抱歉，这条业务请求暂时无法生成结构化分析" not in body.get("content", ""), body
    assert "敏感度" in body.get("content", "") or "安全边际" in body.get("content", ""), body


def test_attribution_intent_returns_decomposition_not_missing_gap():
    """端到端：成本归因不得恒报「补充：月营收」。

    月营收是**派生值**，只存在于 scan.core_metrics；出口 params 没有它，
    归因又只从 params 取 → 只要 pixelizer 不补，该意图永远不可用。
    """
    tid = "rb-attribution-reg"
    _chat([{"role": "user",
            "content": "开奶茶店，月租金1万，日售50杯，单价15，变动成本率60%，员工2人工资各5000"}],
          tid=tid)

    r = _chat([{"role": "user", "content": "成本归因拆解"}], tid=tid)
    assert r.status_code == 200
    body = r.json()
    content = body.get("content", "")
    assert "补充：月营收" not in content, f"归因仍缺月营收: {content[:200]}"
    # 应给出真实分解数据
    assert "成本归因" in content or "占比" in content, content[:200]
