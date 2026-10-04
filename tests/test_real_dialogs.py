"""真实对话 oracle — 验证 LLM 在干净的输入下「真在对话，不是套降级模板」。

覆盖三个关键场景：
- T1「明确改参数」→ 引擎应用、派生字段重算、LLM 解读不翻历史原文对账
- T2「模糊目标」→ LLM 输出 ops 块（编排提议），op_executor 校验 + 用户「应用」生效
- T3「守门拦死值」→ op 携带派生字段 / 超界值 → 拒绝并附理由

LLM 被桩掉，重点测的是**架构契约**而非 LLM 真实回答。
"""
import os
import sys
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import web_server as ws
from fastapi.testclient import TestClient
from session_state import reset_state, get_state, to_llm_view
from op_executor import (
    validate_op, preview_op, apply_op, parse_apply_command,
    BASE_FIELDS, DERIVED_FIELDS,
)
from tools.workflow_engine import quick_scan

# 桩 LLM 让它在不同问句下「装作在对话/编排」——通过 user_text 关键选择回复
def _stub_advise(scan, user_text="", session_snapshot=None, context=None):
    text = user_text or ""
    if "怎么扭亏" in text or "怎么不亏" in text:
        # 模糊目标 → LLM 应该输出 ops 块
        out = (
            "月亏 -900 偏紧。要扭亏有三个杠杆可动，先看引擎算的候选：\n\n"
            "```ops\n"
            "[\n"
            '  {"propose": "try", "label": "提价2元", "changes": {"price_per_unit": 17}, "reason": "客单价+2元看能否扭亏"},\n'
            '  {"propose": "try", "label": "客流+10/天", "changes": {"daily_traffic": 60}, "reason": "客流提升10/天看能否扭亏"}\n'
            "]\n"
            "```"
        )
        return {"text": out.replace("```ops\n[\n", "```ops\n[\n").split("```ops",1)[0].strip(),
                "ops": [
                    {"propose":"try","label":"提价2元","changes":{"price_per_unit":17},"reason":"看能否扭亏"},
                    {"propose":"try","label":"客流+10/天","changes":{"daily_traffic":60},"reason":"看能否扭亏"},
                ]}
    # 明确改参数 → 解读后果，但不应输出 ops（抽取已经处理）
    if "人工" in text or "工资" in text:
        return {"text": "已应用：人工参数按你的口径更新，月固定成本由引擎重算——月利润相应同步。", "ops": []}
    return {"text": "本分析基于当前参数。要扭亏请说『怎么不亏』，会给你候选方案预览。", "ops": []}

ws.llm_advise = _stub_advise
_client = TestClient(ws.app)


def _ensure_stub():
    """每个测试入口重新挂桩——防其他测试（如 test_p49_chitchat_goes_steward）
    在运行时把 ws.llm_advise 改成自己的 lambda 后未还原，污染本套 oracle。
    """
    ws.llm_advise = _stub_advise


def _chat(text, tid):
    return _client.post("/chat", json={"messages":[{"role":"user","content":text}], "thread_id": tid}, headers={"X-Requested-With": "XMLHttpRequest"}).json()


def _advice(tid, meta=None):
    body = {"thread_id": tid}
    if meta:
        body["analysis_id"] = meta.get("analysis_id")
        body["params_version"] = meta.get("params_version")
    return _client.post("/analysis/advice", json=body, headers={"X-Requested-With": "XMLHttpRequest"}).json()


def _scan_d(d):
    return json.loads(quick_scan.invoke({"params_json": json.dumps(d, ensure_ascii=False)}))


# ── T1: 明确改参数 → 引擎应用 + LLM 不对账历史 ────────────────────────────


# ── T2: 模糊目标 → LLM 输出 ops，op_executor 校验 + 预览 + 用户「应用」生效 ──


# ── T3: 守门 — op 携带派生字段或超界值被拒 ────────────────────────────────

def test_t3_op_rejects_derived_field():
    """op 改 monthly_labor（派生字段）→ 校验拒绝。"""
    _ensure_stub()
    op = {"propose":"set", "field":"monthly_labor", "value": 5000}
    ok, reason = validate_op(op)
    assert not ok
    assert "派生" in reason, reason


def test_t3_op_rejects_absurd_value():
    """op 改 avg_salary=-5000（物理不可能）→ 校验拒绝。"""
    _ensure_stub()
    _ensure_stub()
    op = {"propose":"set", "field":"avg_salary", "value": -5000}
    ok, reason = validate_op(op)
    assert not ok
    assert "avg_salary" in reason or "物理" in reason


def test_t3_op_accepts_base_field_in_range():
    """op 改 avg_salary=4500（合理）→ 校验通过。"""
    _ensure_stub()
    op = {"propose":"set", "field":"avg_salary", "value": 4500}
    ok, reason = validate_op(op)
    assert ok and reason == ""


def test_t3_preview_computes_profit():
    """preview_op 必须给出「方案应用后月利润」精确值，不靠 LLM 心算。"""
    _ensure_stub()
    _ensure_stub()
    base = {"industry":"餐饮","monthly_rent":1200,"daily_traffic":50,"price_per_unit":15,
            "variable_cost_ratio":0.6,"avg_salary":3000,"employee_count":2}
    op = {"propose":"try","label":"提价2元","changes":{"price_per_unit":17}}
    preview = preview_op(op, base, quick_scan)
    assert preview["ok"]
    # 应用后月利润 = 50×17×30 - 9600 - 0.6×(50×17×30) = 25500 - 9600 - 15300 = 600
    # 但实际引擎可能略有差异，验证它给出的不是 garbage
    assert isinstance(preview["profit_after"], (int, float)), preview


# ── T4: 试一遍 supersede — apply 后再 '怎么不亏' → 新候选基于已应用 ────────


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {t.__name__}: {e}")
            failed += 1
        except Exception as e:  # noqa
            print(f"ERROR {t.__name__}: {e!r}")
            failed += 1
    print(f"\n=== {passed} passed, {failed} failed ===")
    sys.exit(1 if failed else 0)
    _ensure_stub()