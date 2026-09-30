"""响应视图契约：任何 chat 响应路径都必须带 params，且派生值要投影出来。

被测的三条契约（各自独立，缺一条都会让前端说谎）：

1. **params 键完整性**：chitchat 与业务兜底响应此前没有 `params` 键。前端 F3
   采纳校验拿不到 params → 提示「未采纳（当前为-）」，而参数其实已写进 session，
   是纯误导。原则：任何 chat 响应路径都必须带投影后的 params。
2. **派生值投影**：`unit_variable_cost ÷ price` 能推出的变动成本率，在 session
   params 里并不存在（session 只存用户直述）。前端参数面板
   `if (lastParams[f] == null) return` 会直接不渲染 → 报表里写着 80%，
   参数面板里却找不到、也改不了。
3. **投影不篡权**：用户直述的率与物理量推算冲突时，返回的仍是用户说的那个值，
   冲突交给一致性规则提示，投影层不替用户选。

另有一条**防漂移**测试：`_CORE_PARAM_FIELDS` 必须覆盖抽取层实际会产出的字段，
否则抽得出却不认作改参，整句落进 chitchat（response 走 steward，不出仪表盘）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
for _p in (os.path.join(ROOT, "src"), ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_saved = os.getcwd()
import web_server as ws  # noqa: E402
os.chdir(_saved)

from fastapi.testclient import TestClient  # noqa: E402

ws.llm_advise = lambda scan, user_text="", session_snapshot=None, context=None: {"text": "", "ops": []}
_client = TestClient(ws.app)
_XHR = {"X-Requested-With": "XMLHttpRequest"}


def _chat(text, tid="vc"):
    return _client.post("/chat", json={"messages": [{"role": "user", "content": text}],
                                       "thread_id": tid}, headers=_XHR)


# ── 契约 1：params 键完整性 ──────────────────────────────────────────────

def test_chitchat_response_carries_params():
    """纯闲聊响应也带 params：否则前端显示「未采纳（当前为-）」。"""
    _chat("月租金8000，客单价30，客流80", tid="vc-chat-1")
    r = _chat("你好啊", tid="vc-chat-1")
    assert r.status_code == 200
    body = r.json()
    assert "params" in body, "chitchat 响应缺 params 键，前端 F3 会误报未采纳"
    assert body["params"].get("monthly_rent") == 8000


# ── 契约 2：派生值投影 ───────────────────────────────────────────────────

def test_projection_adds_derived_variable_cost_ratio():
    """只给物理量（每份成本 + 客单价）时，响应也要能拿到算出来的率。"""
    r = _chat("每份成本10元，客单价30，客流80，月租8000", tid="vc-proj-1")
    p = r.json().get("params") or {}
    assert p.get("variable_cost_ratio") is not None, "派生率未投影，参数面板会缺这个字段"
    assert abs(p["variable_cost_ratio"] - 10 / 30) < 1e-9


# ── 契约 3：投影不篡权 ───────────────────────────────────────────────────

def test_projection_keeps_user_stated_ratio():
    """用户直述的率优先于物理量推算；投影只补空缺，不覆盖。"""
    r = _chat("每份成本12元，客单价15，变动成本率75%，客流100，月租8000", tid="vc-proj-2")
    p = r.json().get("params") or {}
    assert p.get("variable_cost_ratio") == 0.75
    assert p.get("unit_variable_cost") == 12.0, "物理量不能因为率被直述就丢掉"


# ── 一致性：物理量口径 vs 用户直述率 ─────────────────────────────────────

def test_unit_cost_vs_stated_ratio_flagged():
    from field_model import consistency_issues
    issues = consistency_issues({"unit_variable_cost": 12.0, "price_per_unit": 15.0,
                                 "variable_cost_ratio": 0.75})
    assert len(issues) == 1 and "80%" in issues[0]["message"]


def test_unit_cost_vs_stated_ratio_no_false_positive():
    """率是物理量推出来时两者恒等，不得误报。"""
    from field_model import consistency_issues
    assert consistency_issues({"unit_variable_cost": 12.0, "price_per_unit": 15.0,
                               "variable_cost_ratio": 0.8}) == []
    assert consistency_issues({"variable_cost_ratio": 0.75}) == []


def test_engine_surfaces_unit_cost_conflict():
    """端到端：规则写对了但没接线的话，单测仍全绿 —— 这条防「假绿」。"""
    _chat("每份成本12元，客单价15，变动成本率75%，客流100，月租8000", tid="vc-e2e-1")
    r = _chat("再算一遍", tid="vc-e2e-1")
    assert "80%" in (r.json().get("content") or "")


# ── 防漂移：核心字段清单必须跟上抽取层 ───────────────────────────────────

_SAMPLES = [
    "月租金8000", "月营收2万", "总投资10万", "客单价30", "日均客流80",
    "3人", "月薪5000", "变动成本率60%", "每份成本10元", "毛利率50%",
    "月利润1万", "水电费500", "包装费300", "佣金800", "其他固定成本500",
    "月固定成本2万",
]


def test_core_param_fields_cover_all_extracted_numeric_fields():
    """抽取层产出的每个数值字段都必须在核心清单里，否则改参句会落进 chitchat。

    这是枚举式消费方的典型漂移：抽取层加了新字段，清单不会自己跟上，
    而且单测不跨模块比对，只能靠这条断言兜住。
    """
    from router.intent import _CORE_PARAM_FIELDS
    from router.param_extractor import extract_params
    missing = set()
    for s in _SAMPLES:
        p = extract_params(s) or {}
        missing |= {k for k in p if not k.startswith("_")} - _CORE_PARAM_FIELDS
    assert not missing, f"抽取层产出了核心清单不认的字段：{sorted(missing)}"


# ── 死机制删除契约：用户说过的率永不被静默清除 ────────────────────────────

def test_user_stated_ratio_survives_dependency_change():
    """先说「率60%」，后改「客单价30」——率必须保留，不能被静默清掉。

    这条钉死的是已删除的「清覆盖」机制不再回来：
    clear_stale_overrides + B_FIELD_DEPENDENCIES 此前设计意图是「依赖变化时
    清掉旧 B 类用户覆盖」，实测发现它从 Initial commit 起就从未工作过
    （del user_overrides[b_field] 原地删了传入的同一个 dict，diff 恒为空集）。
    即使修好它，它也是在替用户猜——财务工具最恶劣的失败模式。故删除而非修复。

    矛盾改由一致性规则提示（test_unit_cost_vs_stated_ratio_flagged 已覆盖）。
    """
    import session_state as ss
    tid = "vc-stale"
    st, _ = ss.apply_turn_guarded(tid, {"price_per_unit": 25.0,
                                        "variable_cost_ratio": 0.6}, "T1")
    st, _ = ss.apply_turn_guarded(tid, {"price_per_unit": 30.0}, "T2")
    p2 = {k: v for k, v in st["params"].items() if not k.startswith("_")}
    assert p2.get("variable_cost_ratio") == 0.6, "用户说过的率被静默清掉了"
    # state 不再保留这个曾经装死机制的键
    assert "user_overrides" not in st
