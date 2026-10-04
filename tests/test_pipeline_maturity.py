"""运行链路成熟度 oracle（界面口语句 → 抽取 → 引擎 → 不得静默算错）。

来源：2026-09-14 improve-prompt 五维审查。门禁 428 全绿时，旗舰口语句仍会：
  - 把「工资一共」当人均（人工×2）
  - 漏抽「食材大概35%」
  - 把年租金当月租
  - 无标点连写把客单价串成几十万
  - 缺变动成本率却报跑道「无限」/正向现金流
  - 「还是/如果」误入 compare 不入 session
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from router.param_extractor import extract_params
from router.intent import detect_intent
from tools.workflow_engine import quick_scan, _fill_params
from router.formatter import format_response

TOL = 1e-6
FLAGSHIP = (
    "开家牛肉面店，投资20万，月租8000，两个人工资一共1万2，"
    "一天大概80碗，一碗18块，食材大概35%"
)
NOPUNCT = (
    "开咖啡店投资30万租金8000两个人工资一共1万2"
    "一天大概80单一杯25食材成本大概35%"
)


def _p(text):
    return {k: v for k, v in extract_params(text).items() if not k.startswith("_")}


def _scan(text):
    return json.loads(quick_scan.invoke(
        {"params_json": json.dumps(_p(text), ensure_ascii=False)}))


# ── S2 抽取 ──────────────────────────────────────────────────────────────


def test_mat_deposit_months_not_rent():
    """「押金3个月房租」不得把月租抽成 3。"""
    p = _p("押金3个月房租")
    assert p.get("monthly_rent") != 3.0, p


def test_mat_commission_percent_not_yuan():
    p = _p("美团抽成20%")
    assert p.get("commission") != 20.0, p


# ── S3 意图 ──────────────────────────────────────────────────────────────


def test_mat_deposit_not_trend():
    intent, _ = detect_intent("押金3个月房租")
    assert intent != "trend", intent


# ── S4 引擎：缺失不当 0 ──────────────────────────────────────────────────


# ── S5 守门：餐饮客单价串台剔除 ──────────────────────────────────────────

def test_mat_absurd_price_stripped_for_catering():
    raw = extract_params("开家面馆客单价300000元")
    assert raw.get("price_per_unit") in (None, 0) or raw.get("price_per_unit", 0) < 1000, raw
    g = extract_params("开家面馆客单价300000元").get("_guard") or {}
    # 若抽取到荒谬值，必须 critical + 剔除
    if "price_per_unit" in {k for k in extract_params("开家面馆客单价300000元")}:
        assert False, "荒谬客单价不得进入 cleaned"
