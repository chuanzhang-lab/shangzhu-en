"""Phase 4 验收测试：工作台一体化（引擎补全 + 路由收口 + 抽取器防误抓）。

运行（无需 pytest）：.venv/bin/python3 tests/test_phase4_workbench.py
若装了 pytest，会被自动收集。

覆盖（随实施逐步填充，与 PLAN.md (f) 逐步施工清单一一对应）：
- P4-0 固定成本组件化：rent+labor+util+pack+comm+other 自动求和 = 22060，来源[用户]
- P4-1 单位变动成本：每份12 + 客单价15 → 变动率 0.80(推导)；显式优先
- P4-2 营收派生：显式2万优先于派生1.8万
- P4-4 抽取器防误抓：问句/修辞不捞 monthly_fixed_cost=1.0
- P4-5 跑道：profit=-8000,cash=80000 → runway≈10；cash 缺失→[缺失]
- P4-3 路由收口：业务路径无 get_agent()（grep）
- P4-7 引擎管理者：advise 只读快照、what-if 调 compare_scenarios
- 12 轮羊肉汤店对话集成 oracle（T1~T14）
"""
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from router.param_extractor import extract_params
from router.intent import detect_intent
from tools.workflow_engine import quick_scan, _fill_params
from tools.financial_calculator import _calc_runway as fc_calc_runway
from session_state import merge_params, apply_turn, reset_state
from llm_advisor import advise, _emit_anomaly_report
import llm_advisor as _llm_mod
import inspect

TID = "test-phase4"


def _scan(params_dict):
    """经引擎唯一计算点 quick_scan（@tool）出仪表盘 dict。"""
    return json.loads(quick_scan.invoke({"params_json": json.dumps(params_dict, ensure_ascii=False)}))


def _extract(text):
    return extract_params(text)


def test_phase4_skeleton_ready():
    """S4-0 占位：骨架已就位、基建可收集。实施中逐步替换为真断言。"""
    assert callable(_scan) and callable(_extract)


# ── P4-0.1 抽取器：固定成本组件字段 ───────────────────────────────────────


def test_extract_no_over_trigger():
    """「其他」不该在无关修辞里误抓数字。"""
    p = _extract("其他的我后面再说")
    assert "other_fixed" not in p, p


# ── P4-0.2 / P4-0.3 引擎：固定成本 C1 聚合 ─────────────────────────────────


def test_c1_partial_no_total():
    """组件不全且无显式总数：只算已知组件(租金)，不索要总数、不虚构人工。"""
    params = {"monthly_rent": 12000, "monthly_revenue": 20000, "total_investment": 200000}
    d = _scan(params)
    # 只给了租金 → fixed=12000（人工未给，不计入也不虚构）
    assert d["params"]["monthly_fixed_cost"] == 12000, d["params"]["monthly_fixed_cost"]
    # 来源标签串按 i18n 同 key 取 en 值：src.fixed_cost_sum_incomplete → "... Component sum (...)"
    assert "Component sum" in d["param_sources"]["monthly_fixed_cost"]


# ── P4-1 单位变动成本 ─────────────────────────────────────────────────────

def test_extract_unit_variable_cost():
    """「每份成本12元」→ unit_variable_cost=12，且不误抽成客单价。"""
    p = _extract("每份成本12元")
    assert p.get("unit_variable_cost") == 12.0, p
    assert "price_per_unit" not in p, p


def test_p41_derives_ratio():
    """unit12 + 客单价15 → 变动率 0.80（标[推导]）。测引擎内部值(float)。"""
    p, src, _ = _fill_params({"unit_variable_cost": 12, "price_per_unit": 15,
                              "monthly_revenue": 20000, "total_investment": 200000})
    assert abs(p["variable_cost_ratio"] - 0.80) < 1e-6, p["variable_cost_ratio"]
    # 中文「推导」专指 src.derived.vcr_from_unit_cost（"[推导] 单位变动成本÷客单价"）；
    # en 侧该 key 值为 "[Derived] Unit variable cost ÷ unit price"——mark 与其它派生
    # 同为 [Derived]，故断言该 key 的独有短语（等价且不弱于中文侧的判别力）。
    assert "Unit variable cost ÷ unit price" in src["variable_cost_ratio"]


# ── P4-2 营收派生（C2：显式优先，不被派生覆盖）──
# 注：核实当前 _fill_params 营收块已满足 C2（显式优先、缺失才派生），
# 故 P4-2 仅固化行为，无需改代码。


def test_p42_derive_only_when_missing():
    """无显式营收 + 有 price×traffic → 派生 15×40×30=18000[推算]。"""
    p, src, _ = _fill_params({"price_per_unit": 15, "daily_traffic": 40, "total_investment": 200000})
    assert p["monthly_revenue"] == 15 * 40 * 30, p["monthly_revenue"]
    # 中文「推算」= src.mark.derived 标记；en 同 key 为 "[Derived]"（判别力等价）
    assert "[Derived]" in src["monthly_revenue"]


def test_p42_merge_keeps_explicit_revenue():
    """跨轮 merge：显式营收不被后续派生输入覆盖（T1 2万跨轮保留）。"""
    merged = merge_params({"monthly_revenue": 20000}, {"price_per_unit": 15, "daily_traffic": 40})
    assert merged.get("monthly_revenue") == 20000, merged


# ── P4-5 跑道修正（cash 缺失→[缺失]；亏损有现金→有限；仅正向才"无限"）──

def test_p45_finite_runway():
    """cash=80000, burn=28000, rev=20000 → net_burn=8000 → runway=10（有限）。"""
    rw = fc_calc_runway(80000, 28000, 20000)
    assert rw["runway_months"] == 10.0, rw
    assert rw["runway_months"] != "无限"


def test_p45_missing_cash():
    """current_cash 为 None → [缺失]，不计算、不"无限"。"""
    rw = fc_calc_runway(None, 28000, 20000)
    assert rw.get("runway_months") is None, rw
    # note 串按 i18n 同 key 取 en 值：fc.runway.cash_missing → "Cash missing; runway cannot be computed"
    assert "Cash missing" in rw.get("note", "")


def test_p45_positive_infinite():
    """仅正向现金流(net_burn<=0) 才"无限"（真实不烧钱）。"""
    rw = fc_calc_runway(50000, 10000, 20000)
    assert rw["runway_months"] == "无限", rw


def test_p45_e2e_loss_not_infinite():
    """端到端：亏损场景跑道有限（burn 含变动成本，不再误判"无限"）。"""
    params = {"monthly_rent": 12000, "employee_count": 2, "avg_salary": 4000,
              "utilities": 800, "packaging": 260, "commission": 1000,
              "monthly_revenue": 20000, "total_investment": 200000,
              "variable_cost_rate": 75}
    d = _scan(params)
    rw = d["runway"]["runway_months"]
    assert rw != "无限" and rw is not None, rw
    assert isinstance(rw, (int, float)), rw


# ── P4-4 抽取器防误抓（strict_units）──

def test_p44_no_miscatch_rhetoric():
    """T14：修辞问句「我把月固定成本每一项都给你了」不应误抽 monthly_expense（原 bug→1.0）。"""
    p = _extract("我把月固定成本每一项都给你了，还得给总数？")
    assert "monthly_expense" not in p, p


# ── P4-3 路由收口（业务问句归引擎，不落 chitchat/get_agent）──


def test_p43_business_path_no_get_agent():
    """web_server 业务路径(_route_intent 函数体)不得调用 get_agent；且 breakeven 已接入。"""
    import os, re
    ws_path = os.path.join(os.path.dirname(__file__), "..", "web_server.py")
    src = open(ws_path, encoding="utf-8").read()
    start = src.index("def _route_intent")
    rest = src[start + 10:]
    m2 = re.search(r"\n(def|class) ", rest)
    end = start + 10 + (m2.start() if m2 else len(rest))
    body = src[start:end]
    assert "get_agent" not in body, "业务路径(_route_intent)不应调用 get_agent"
    assert '"breakeven"' in src, "breakeven 未接入 _PROJECT_INTENTS"


# ── P4-7 引擎管理者（Engine Steward）──

def test_p47_advise_readonly_no_mutation():
    """S4-7.1：advise 对 scan/session_snapshot 只读——即便无 key 返回空，入参也不被改动。
    
    API key 现在优先从 config 读取，需同时 mock config 和 env。
    """
    from unittest.mock import patch
    saved = os.environ.get("DEEPSEEK_API_KEY")
    os.environ["DEEPSEEK_API_KEY"] = ""
    try:
        scan = {"params": {"monthly_fixed_cost": 12000}, "param_sources": {}}
        snap = {"params": {"monthly_revenue": 20000}}
        before_scan = {"params": dict(scan["params"]), "param_sources": dict(scan["param_sources"])}
        before_snap = {"params": dict(snap["params"])}
        with patch.object(_llm_mod, "_load_llm_config", return_value={"config": {"model": "test", "api_key": "", "base_url": "http://localhost"}}):
            _llm_mod._llm_cache = None
            out = advise(scan, "测试", session_snapshot=snap)
        assert isinstance(out, dict)
        assert out.get("text") == ""  # 无 key → 空
        assert out.get("ops") == []
        # 入参未被改动（深拷贝只读护栏生效）
        assert scan == before_scan, "scan 被改动"
        assert snap == before_snap, "session_snapshot 被改动"
    finally:
        _llm_mod._llm_cache = None
        if saved is None:
            os.environ.pop("DEEPSEEK_API_KEY", None)
        else:
            os.environ["DEEPSEEK_API_KEY"] = saved


def test_p47_advise_no_eval_exec():
    """S4-7.1/S4-7.4（C3 红线）：advise 函数体不得出现 exec(/eval( 自由执行。"""
    src = inspect.getsource(advise)
    assert "exec(" not in src, "advise 不得含 exec(（C3 红线）"
    assert "eval(" not in src, "advise 不得含 eval(（C3 红线）"


def test_p47_anomaly_report_catches_extreme_fixed():
    """S4-7.4：极端固定成本（疑似抽取误抓 1.0）被检出。"""
    scan = {"params": {"monthly_fixed_cost": 1.0}, "param_sources": {}}
    report = _emit_anomaly_report(scan)
    assert report is not None, "应检出极端固定成本异常"
    assert any(a["field"] == "monthly_fixed_cost" for a in report["anomalies"])


def test_p47_anomaly_report_clean():
    """S4-7.4：正常数据不报异常（不误伤）。"""
    scan = {
        "params": {"monthly_fixed_cost": 22060},
        "param_sources": {"monthly_fixed_cost": "[用户]组件求和"},
    }
    assert _emit_anomaly_report(scan) is None


# ── P4-6 集成回归 oracle（12 轮羊肉汤店对话，逐轮锁行为）──────────────────
# 说明：朴素中文表述经抽取器解析后可能歧义（如"租金+人工20000"会被当 rent=20000，
# "员工2人共8000"不会抽薪资）。此处用「用户意图等价、引擎能正确解析」的表述回放，
# 锁定的是契约行为（C1 自动求和 / C2 营收优先 / C3 路由收口 / 跑道有限 / 不误抓1.0），
# 与 PLAN.md (d) 节 12 轮 oracle 逐行对应。
#
# E-03：轮次文本是「用户输入」，随部署语言走英文；每条与中文原文在 zh 下的
# extract_params / detect_intent 结果逐字段一致（同参数、同意图、同数值）。
# 例外说明：T11 中文「日均40」（裸日均、无量词）本就不抽 daily_traffic
# （D9 只认「日均+数字+量词」），英文侧用 "about 40/day" 同样不触发客流抽取，
# 保持两侧抽取结果一致，而不是把一侧的漏抽补成另一侧的多抽。

_TURNS = {
    "T1":       "Open a mutton soup shop, total investment 200000, unit price 15, 2 employees at 4000 each, monthly revenue 20000",
    "T3":       "monthly rent 8000, variable cost rate 40%, how do I break even?",
    "T4":       "total fixed costs 20000, unit price 15, 40 customers a day",
    "T5":       "rent 12000, 2 employees at 4000 each, other fixed 1000",
    "T9":       "mutton soup shop: cost per bowl 12, utilities 800, packaging 260, commission 1000",
    "T10":      "is it better to halve the monthly rent to 6000, or raise the unit price to 20?",
    "T11":      "variable cost rate 75%, unit price 15, about 40/day, monthly rent 12000",
    "T12":      "change monthly rent to 6000, how many customers a day to break even?",
    "T14":      "I already gave you every item of the monthly fixed costs; do you still need a total?",
    "T_rent12": "monthly rent 12000",
    "T_rent6":  "monthly rent 6000",
    "T_ratio75":"variable cost rate 75%",
}


def _oracle_replay(keys, tid="p4-6-oracle"):
    """按给定轮次顺序回放（跨轮 merge，唯一真相源），返回最后一帧 (merged, scan)。"""
    reset_state(tid)
    merged = None
    for k in keys:
        text = _TURNS[k]
        params = extract_params(text)
        st = apply_turn(tid, params, text, params.get("industry", ""))
        merged = st["params"]
    return merged, _scan(merged)


def test_p46_t3_breakeven_routed_and_computed():
    """T3：月租金8000 + 收支平衡问句 → 固定=8000+8000=16000[用户]自动求和；
    breakeven 问句路由引擎(意图=breakeven)，引擎算保本客流；不追问总数。"""
    merged, d = _oracle_replay(["T1", "T3"])
    assert d["params"]["monthly_fixed_cost"] == 16000, d["params"]["monthly_fixed_cost"]
    # 来源标签串按 i18n 同 key 取 en 值：src.fixed_cost_sum → "{mark} Component sum ({comp})"
    assert "Component sum" in d["param_sources"]["monthly_fixed_cost"]
    # 「待澄清」的 en 同 key 串是 "to clarify"（fixed_cost_sum_conflict 尾注）
    assert "to clarify" not in d["param_sources"]["monthly_fixed_cost"]  # 无矛盾，不追问
    # 路由收口：保本问句归引擎
    intent, _ = detect_intent(_TURNS["T3"])
    assert intent == "breakeven", intent
    # 引擎算出保本日客流（≈58，finite，非编造）
    db = d["core_metrics"]["daily_breakeven"]
    assert isinstance(db, (int, float)) and 50 <= db <= 70, db


def test_p46_t4_contradiction_not_silent():
    """T4：组件和=16000 与 显式总数20000 矛盾 → 标记矛盾交 steward 提问，
    绝不静默取20000；营收仍2万(C2)。"""
    merged, d = _oracle_replay(["T1", "T3", "T4"])
    assert d["params"]["monthly_fixed_cost"] == 16000, d["params"]["monthly_fixed_cost"]
    # 中文「矛盾」出自 src.fixed_cost_sum_conflict 文案「…与显式总数{total}矛盾→待澄清」；
    # en 同 key 为 "{mark} Component sum ({comp}) conflicts with explicit total {total} → to clarify"
    # （mark 取组件状态码 [User]，非 [Conflict]），判别词是 "conflicts"。
    assert "conflicts" in d["param_sources"]["monthly_fixed_cost"], d["param_sources"]["monthly_fixed_cost"]
    # 营收显式2万不被覆盖
    assert d["core_metrics"]["monthly_revenue"] == 20000
    assert d["param_sources"]["monthly_revenue"].startswith("[User]")


def test_p46_t9_fixed_sum_and_derived_ratio():
    """T9：每份12+水电800+包装260+提成1000 → fixed=12000+8000+800+260+1000=22060[用户](人工无默认负担)；
    unit12+price15 → ratio=0.80[推导](C1核心断言)。"""
    merged, d = _oracle_replay(["T1", "T_rent12", "T9"])
    assert d["params"]["monthly_fixed_cost"] == 22060, d["params"]["monthly_fixed_cost"]
    assert "Component sum" in d["param_sources"]["monthly_fixed_cost"]
    # 出口为数值契约（0~1）；百分比展示由前端格式化，不要在这里断言展示串
    assert abs(d["params"]["variable_cost_ratio"] - 0.8) < 1e-9, d["params"]["variable_cost_ratio"]
    # 「推导」→ en 同 key（src.derived.vcr_from_unit_cost）独有短语，见 test_p41 注释
    assert "Unit variable cost ÷ unit price" in d["param_sources"]["variable_cost_ratio"]


def test_p46_t12_breakeven_minimal_14000():
    """T12：月租改6000 + 保本问句 → fixed=6000+8000=14000[用户]自动求和(人工无默认负担)；
    引擎算保本客流≈150/天，数全部来自引擎（C3）。"""
    intent, _ = detect_intent(_TURNS["T12"])
    assert intent == "breakeven", intent
    merged, d = _oracle_replay(["T1", "T_rent6", "T_ratio75"])
    assert d["params"]["monthly_fixed_cost"] == 14000, d["params"]["monthly_fixed_cost"]
    db = d["core_metrics"]["daily_breakeven"]
    assert isinstance(db, (int, float)) and 100 <= db <= 165, db


def test_p46_t14_no_miscatch():
    """T14：修辞问句「我把月固定成本每一项都给你了，还得给总数？」
    抽取器不误抓 monthly_fixed_cost=1.0；回应"已自动加总，无需你算"。"""
    p = extract_params(_TURNS["T14"])
    assert "monthly_expense" not in p, p
    # 任何固定成本相关字段都不该是 1.0 误抓
    for k, v in p.items():
        if "fixed" in k or "expense" in k:
            assert v != 1.0, f"{k}={v} 误抓"


def test_p46_full_replay_invariants():
    """全程：线性重放 T1→T3→T4→T5→T9→T10→T11→T12→T14，逐轮锁：
    营收恒2万[用户]、avg_salary恒4000[用户]、跑道有限(非无限/非None)、
    绝不误抓 monthly_fixed_cost=1.0。"""
    reset_state("p4-6-full")
    for k in ["T1", "T3", "T4", "T5", "T9", "T10", "T11", "T12", "T14"]:
        text = _TURNS[k]
        params = extract_params(text)
        st = apply_turn("p4-6-full", params, text, params.get("industry", ""))
        d = _scan(st["params"])
        # 营收恒定
        assert d["core_metrics"]["monthly_revenue"] == 20000, (k, d["core_metrics"]["monthly_revenue"])
        # 中文「[用户]」标记 → en 同 key（src.mark.user）值 "[User]"
        assert d["param_sources"]["monthly_revenue"].startswith("[User]"), k
        # 薪资恒定
        assert d["params"]["avg_salary"] == 4000, (k, d["params"]["avg_salary"])
        # 现金不缺失（总投资已给 → available_cash 存在）。
        # 变动成本率缺失时跑道必须是未知，不得把变动成本当 0 算出「无限」。
        rw = d["core_metrics"]["runway_months"]
        if d["core_metrics"]["monthly_profit"] is None:
            # 中文「未知」是展示文案（wf.common.unknown），en 同 key 值为 "Unknown"
            assert rw is None or rw == "Unknown", (k, rw, "缺变动成本率时跑道应未知")
        else:
            assert rw is not None, (k, "跑道缺失(cash缺失bug)")
        # 亏损时跑道必须有限（修旧"无限"假象）：仅盈利(net_burn<=0)才"无限"。
        # "无限" 是引擎哨兵 INFINITE_MARK（数据标记，保持原样）；出口 _rw_display
        # 在 en 下把它映射成 fmt.common.infinite="Unlimited"，两个名字都拒收，
        # 否则 en 部署下该护栏对「亏损却显示无限」静默失效。
        if d["core_metrics"]["monthly_profit"] is not None and d["core_metrics"]["monthly_profit"] < 0:
            assert rw != "无限" and rw != "Unlimited", (k, rw)
        # 不误抓 1.0
        assert d["params"]["monthly_fixed_cost"] != 1.0, (k, d["params"]["monthly_fixed_cost"])


# ── P4-8 解耦断言：web_server 不得依赖 Coze 自由 agent（B 非本仓交付物）──

def test_p48_web_server_decoupled_from_coze_agent():
    """S4 解耦硬性保证：本地工作台(web_server)的源码不得 import/调用 agents.agent、
    build_agent、get_agent、langchain_core.messages、get_memory_saver——
    这些属于 Coze 平台路径(B)，本仓只交付引擎层(A)。否则重型依赖(boto3/cozeloop/
    sqlalchemy 等)会被拉入本地服务，破坏自包含。"""
    import os
    ws_path = os.path.join(os.path.dirname(__file__), "..", "web_server.py")
    src = open(ws_path, encoding="utf-8").read()
    # 去掉注释行，避免说明性注释（如「build_agent 非本仓交付物」）造成误判
    code = "\n".join(
        ln for ln in src.splitlines()
        if not ln.lstrip().startswith("#")
    )
    banned = {
        "import agents.agent": "from agents.agent",
        "build_agent 调用": "build_agent(",
        "get_agent 调用": "get_agent(",
        "langchain_core.messages": "langchain_core.messages",
        "get_memory_saver": "get_memory_saver",
    }
    for label, token in banned.items():
        assert token not in code, f"web_server 仍耦合 Coze agent：发现 {label}（{token!r}）"


def test_p48_tests_cover_only_engine_layer():
    """明确化：本测试套件只覆盖本地引擎层(A)，不依赖 Coze 路径(B)的任何模块。
    若日后 Coze 路径被纳入交付，需补独立测试，而非在此隐式耦合。
    判定只看真实 import 语句（避免误伤注释/文档里的字面词）。"""
    import os, re
    test_root = os.path.dirname(__file__)
    # 仅匹配真正的 import 行（允许前导空白），排除注释里的字面提及
    import_re = re.compile(r'^\s*(from\s+\S+\s+import|import\s+\S+)', re.M)
    for fn in os.listdir(test_root):
        if not fn.startswith("test_") or not fn.endswith(".py"):
            continue
        txt = open(os.path.join(test_root, fn), encoding="utf-8").read()
        for m in import_re.finditer(txt):
            stmt = m.group(1)
            assert "agents" not in stmt.split()[0:2], \
                f"{fn} 不应 import agents（Coze 路径）：{stmt}"
            assert "coze_coding_utils" not in stmt and "cozeloop" not in stmt, \
                f"{fn} 不应依赖 Coze 平台库：{stmt}"


# ── P4-9 守护：chitchat 走 Engine Steward（非 Coze 自由 agent）+ 配置单源 ──

def test_p49_chitchat_uses_steward():
    """chitchat 必须走 Engine Steward（llm_advisor，只读接地），绝不引入 Coze 自由 agent。

    用桩替换 web_server.llm_advise，验证 /chat 的 chitchat 分支返回 mode=='steward'
    且内容来自 steward。这同时守卫「web_server 在 import 时不能被 re-exec 守卫劫持成
    uvicorn 服务卡死」——此前该 bug 正是 p49 测试一度被回退的根因。
    """
    import os, sys
    # web_server.py 在仓库根目录，且导入时会 os.chdir 到 src/，
    # 因此必须把仓库根目录加入 path 才能 import。
    _root = os.path.join(os.path.dirname(__file__), "..")
    if _root not in sys.path:
        sys.path.insert(0, _root)
    import web_server as ws
    from fastapi.testclient import TestClient

    saved_cwd = os.getcwd()
    try:
        # 桩：让 steward 返回可识别内容（不真实联网）
        ws.llm_advise = lambda scan, user_text="", session_snapshot=None, context=None: "【STEWARD_OK】"
        c = TestClient(ws.app)
        resp = c.post("/chat", json={
            "messages": [{"role": "user", "content": "随便聊聊，今天心情不错"}],
            "thread_id": "p49-steward",
        }, headers={"X-Requested-With": "XMLHttpRequest"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body.get("mode") == "steward", body
        assert body.get("intent") == "chitchat", body
        assert "STEWARD_OK" in body.get("content", ""), body
    finally:
        os.chdir(saved_cwd)


def test_p49_health_and_footer_single_source():
    """配置单源护栏：/health 的 model、首页页脚的模型名、web_server.MODEL_NAME
    都必须等于 config/agent_llm_config.json 的 config.model；且 web_server 源码不得
    出现硬编码模型名（否则改 json 不生效，形成配置漂移）。
    """
    import os, json, sys
    cfg_path = os.path.join(os.path.dirname(__file__), "..", "config", "agent_llm_config.json")
    with open(cfg_path, encoding="utf-8") as f:
        model = json.load(f)["config"]["model"]

    _root = os.path.join(os.path.dirname(__file__), "..")
    if _root not in sys.path:
        sys.path.insert(0, _root)
    import web_server as ws
    from fastapi.testclient import TestClient

    saved_cwd = os.getcwd()
    try:
        # 1) 模块级 MODEL_NAME == config.model
        assert ws.MODEL_NAME == model, (ws.MODEL_NAME, model)
        # 2) /health 返回 model == config.model；endpoint 属内部详情，需显式索取
        c = TestClient(ws.app)
        h = c.get("/health").json()
        assert h["model"] == model, h
        assert "endpoint" not in h
        assert c.get("/health?detail=1").json().get("endpoint"), h
        # 3) 首页页脚占位符被真实模型名替换，且不再残留占位符
        html = c.get("/").text
        assert model in html, "页脚未注入真实模型名"
        assert "__MODEL_NAME__" not in html, "页脚占位符未被替换"
        # 4) 源码不得硬编码模型名（去除注释行后再查，允许说明性注释提及）
        #    注意：model 为空（全新克隆尚未配置模型）时 `"" in code` 恒为 True，
        #    会造成误报失败。本护栏要防的是「已配置模型名却仍硬编码在源码里」，
        #    未配置时该检查无意义，故仅在 model 非空时执行。
        if model:
            ws_path = os.path.join(os.path.dirname(__file__), "..", "web_server.py")
            code = "\n".join(
                ln for ln in open(ws_path, encoding="utf-8").read().splitlines()
                if not ln.lstrip().startswith("#")
            )
            assert model not in code, "web_server 出现硬编码模型名，破坏配置单源"
    finally:
        os.chdir(saved_cwd)


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
