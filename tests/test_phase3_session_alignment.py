"""Phase 3 验收测试：跨轮对齐层（SessionState + 抽取修复 + LLM 接地）。

运行（无需 pytest）：.venv/bin/python3 tests/test_phase3_session_alignment.py
若装了 pytest，会被自动收集。

覆盖：
- 中文数字解析（一万二→12000 等，修旧「房租一万二」→1.0 的 bug）
- 抽取器：羊肉汤店 Turn1 / Turn2 正确抽取
- 跨轮 merge：两轮合并得到完整参数（消除「信息不全」反复误报）
- 完整仪表盘：merge 后 quick_scan 不再报不足
- 续算意图识别 / 重置命令
- SessionState 接地上下文（真实项目背景，无「成都冒菜店」幻觉）
- 业务消息路由：两轮均命中项目意图（不落 chitchat，从而触发 merge）
"""
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from router.param_extractor import extract_params, _parse_cn_number
from router.intent import detect_intent
from session_state import (
    apply_turn,
    reset_state,
    merge_params,
    is_continuation,
    is_reset_command,
    get_session_context,
    get_state,
)
from tools.workflow_engine import quick_scan

TURN1 = "开羊肉汤店，投资20万，单价15，月营收2万，房租一万二"
TURN2 = "人工成本2人8000，固定成本2500，变动成本率40%，你再算一下"
TID = "test-phase3"


def _scan(params_dict):
    return json.loads(quick_scan.invoke({"params_json": json.dumps(params_dict, ensure_ascii=False)}))


def test_cn_number_parsing():
    cases = {
        "一万二": 12000, "一千五": 1500, "两万": 20000, "二十万": 200000,
        "一百二十万": 1200000, "一": 1, "十二": 12, "十": 10,
        "一万二千": 12000, "一万零二": 10002, "三": 3,
    }
    for s, exp in cases.items():
        got = _parse_cn_number(s)
        assert got == exp, f"{s!r} -> {got} 期望 {exp}"


def test_extract_turn1():
    p = extract_params(TURN1)
    assert p.get("industry") == "餐饮", p
    assert p.get("monthly_rent") == 12000.0, p
    assert p.get("total_investment") == 200000.0, p
    assert p.get("price_per_unit") == 15.0, p
    assert p.get("monthly_revenue") == 20000.0, p


def test_extract_turn2():
    p = extract_params(TURN2)
    assert p.get("employee_count") == 2.0, p
    assert p.get("avg_salary") == 8000.0, p
    assert p.get("monthly_expense") == 2500.0, p


def test_merge_across_turns():
    reset_state(TID)
    apply_turn(TID, extract_params(TURN1), TURN1, "餐饮")
    st = apply_turn(TID, extract_params(TURN2), TURN2, None)
    merged = st["params"]
    for k in ["industry", "monthly_rent", "total_investment", "price_per_unit",
              "monthly_revenue", "employee_count", "avg_salary", "monthly_expense"]:
        assert k in merged and merged[k] not in (None,), f"merge 缺 {k}: {merged}"


def test_full_dashboard_after_merge():
    reset_state(TID)
    apply_turn(TID, extract_params(TURN1), TURN1, "餐饮")
    st = apply_turn(TID, extract_params(TURN2), TURN2, None)
    d = _scan(st["params"])
    assert not d.get("insufficient"), f"merge 后仍报不足: {d.get('gaps')}"
    assert d["params"]["employee_count"] == 2.0
    assert d["params"]["avg_salary"] == 8000.0
    # P4-0 C1：Turn1 租金12000 + Turn2 人工16000(2×8000，无默认社保负担) 已为组件，显式「固定成本2500」
    # 与组件和 28000 矛盾 → 标矛盾而非静默取 2500（旧语义掩盖了真矛盾）
    assert d["params"]["monthly_fixed_cost"] == 28000.0, d["params"]["monthly_fixed_cost"]
    assert "矛盾" in d["param_sources"]["monthly_fixed_cost"]
    # 月营收从 Turn1 带入，未因 Turn2 缺失而丢失
    assert d["core_metrics"]["monthly_revenue"] == 20000.0


def test_merge_is_latest_wins():
    """新轮的非 None 值应覆盖旧值（用户最新输入优先）。"""
    old = {"monthly_rent": 8000, "employee_count": 3}
    new = {"monthly_rent": 12000}
    merged = merge_params(old, new)
    assert merged["monthly_rent"] == 12000
    assert merged["employee_count"] == 3  # 旧值保留


def test_continuation_detection():
    assert is_continuation("人工成本2人8000，你再算一下") is True
    assert is_continuation("重新算一次") is True
    assert is_continuation("开个奶茶店试试") is False


def test_reset_command():
    assert is_reset_command("重新开始") is True
    assert is_reset_command("换个新项目") is True
    assert is_reset_command("开羊肉汤店") is False


def test_session_grounding():
    reset_state(TID)
    apply_turn(TID, extract_params(TURN1), TURN1, "餐饮")
    apply_turn(TID, extract_params(TURN2), TURN2, None)
    ctx = get_session_context(TID)
    assert "餐饮" in ctx, ctx
    # 币种随部署口径走（本部署 USD）—— 不要写死「元」，改币种会误报
    from i18n import t as _t
    assert f"200,000{_t('ss.fmt.yuan')}" in ctx, ctx  # 总投资真实值，无编造
    assert "成都冒菜店" not in ctx  # 杜绝历史幻觉


def test_business_not_chitchat():
    # 羊肉汤店两轮都应命中项目意图（非 chitchat），从而触发 merge
    for t in (TURN1, TURN2):
        intent, _ = detect_intent(t)
        assert intent != "chitchat", f"{t!r} 被误判为 chitchat"
        assert intent in {"quick_scan", "suggest", "trend", "compare",
                          "benchmark", "market"}, intent


def test_pure_param_update_routes_to_quickscan():
    """回归：纯补参句（只给一个数值，无「开/投资/租金」等关键词）必须走
    quick_scan，否则会被误判 chitchat → 既不写 SessionState 也不重算，
    下一轮补参时本轮数据即「被遗忘」（用户在羊肉汤店第 2/3 轮踩中的坑）。"""
    for t in ("月营收20000元", "月租金8000元", "总投资加10万", "客单价改成18"):
        intent, _ = detect_intent(t)
        assert intent == "quick_scan", f"{t!r} 应为 quick_scan，实际 {intent}"


def test_three_turn_accumulation_keeps_revenue():
    """回归：第 2 轮补的月营收，在第 3 轮补租金后不得丢失。

    复现用户真实对话：
      T1 开羊肉汤店，投资20万，客单价15，员工2人
      T2 月营收20000元            ← 曾被误判 chitchat，营收未入 session
      T3 月租金8000元，怎么收支平衡  ← 此时 session 已不含营收 → 误报「信息不全」
    """
    reset_state("repro-3turn")
    t1 = "想开一家羊肉汤店,投资20万，客单价15元,员工2人,共8000元"
    t2 = "月营收20000元"
    t3 = "月租金8000元,你看怎么调整参数才能收支平衡"

    apply_turn("repro-3turn", extract_params(t1), t1, "餐饮")
    # 注意：这里直接走与 chat() 一致的「每轮都累积」逻辑
    p2 = extract_params(t2)
    if p2:
        apply_turn("repro-3turn", p2, t2, p2.get("industry"))
    p3 = extract_params(t3)
    if p3:
        apply_turn("repro-3turn", p3, t3, p3.get("industry"))

    merged = get_state("repro-3turn")["params"]
    assert merged.get("monthly_revenue") == 20000.0, f"营收丢失: {merged}"
    assert merged.get("monthly_rent") == 8000.0, f"租金丢失: {merged}"
    assert merged.get("total_investment") == 200000.0, f"投资丢失: {merged}"
    assert merged.get("employee_count") == 2.0, f"员工丢失: {merged}"

    # 完整仪表盘应可算（不再误报「信息不全」）
    d = _scan(merged)
    assert not d.get("insufficient"), f"3 轮累积后仍报不足: {d.get('gaps')}"


# ─── 独立运行入口（无需 pytest）──────────────────────────────────────────

def test_session_cleanup_limits_memory():
    # 模拟大量会话：应能正常创建，且超过上限后会自动淘汰最旧的
    import session_state as ss
    with ss._LOCK:
        original_sessions = dict(ss._SESSIONS)
        ss._SESSIONS.clear()
        original_max = ss._MAX_SESSIONS
        ss._MAX_SESSIONS = 5  # 临时调小便于验证
    try:
        for i in range(10):
            ss.get_state(f"cleanup-{i}")["params"]["x"] = i
        with ss._LOCK:
            assert len(ss._SESSIONS) <= 5
            # 最新创建的应保留
            assert "cleanup-9" in ss._SESSIONS
    finally:
        with ss._LOCK:
            ss._SESSIONS.clear()
            ss._MAX_SESSIONS = original_max
            ss._SESSIONS.update(original_sessions)


def test_raw_text_truncated():
    """多轮超长原文应被截断，防止 raw_text 无限增长。"""
    import session_state as ss
    tid = "raw-cap"
    reset_state(tid)
    chunk = "字" * 500
    for _ in range(30):
        apply_turn(tid, {"monthly_revenue": 1}, chunk)
    st = get_state(tid)
    assert len(st["raw_text"]) <= ss._MAX_RAW_TEXT_CHARS
    assert st["turn"] == 30


def test_session_stats_shape():
    stats = __import__("session_state", fromlist=["session_stats"]).session_stats()
    assert "active_sessions" in stats
    assert "max_sessions" in stats
    assert "ttl_seconds" in stats
    assert isinstance(stats["active_sessions"], int)


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
