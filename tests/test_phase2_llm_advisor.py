"""Phase 2 验收测试：LLM 协作层（轻量、被动、只读）。

运行（无需 pytest）：.venv/bin/python3 tests/test_phase2_llm_advisor.py
若装了 pytest，会被自动收集。

覆盖：
- _build_brief 在「参数不足」与「带情景」两种场景输出正确；
- advise 在无 key 时返回空且不抛异常（护栏：不阻断主流程）；
- advise 真实 LLM 调用（有 key 才跑，否则跳过，避免无凭证环境失败）。
"""
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tools.workflow_engine import quick_scan
import llm_advisor
from llm_advisor import _build_brief, advise


def _scan(d: dict):
    return json.loads(quick_scan.invoke({"params_json": json.dumps(d)}))


def test_advise_no_key_returns_empty():
    """无 key 时 advise 返回空 text + 空 ops，且不抛异常（护栏：不阻断主流程）。
    
    API key 现在优先从 config 读取，所以需要同时 mock config 和 env。
    """
    import os
    from unittest.mock import patch
    saved = os.environ.get("DEEPSEEK_API_KEY")
    os.environ["DEEPSEEK_API_KEY"] = ""
    try:
        # mock config 返回空 key，模拟无凭证场景
        with patch.object(llm_advisor, "_load_llm_config", return_value={"config": {"model": "test", "api_key": "", "base_url": "http://localhost"}}):
            # 清掉 LLM 缓存，强制重新创建
            llm_advisor._llm_cache = None
            out = advise({"insufficient": True, "gaps": ["月营收"]})
        assert isinstance(out, dict)
        assert out.get("text") == ""
        assert out.get("ops") == []
    finally:
        llm_advisor._llm_cache = None  # 恢复缓存
        if saved is None:
            os.environ.pop("DEEPSEEK_API_KEY", None)
        else:
            os.environ["DEEPSEEK_API_KEY"] = saved


def test_api_key_prefers_config_over_env():
    """API key 配置单源：config 里的 key 优先于环境变量（避免跨厂商错配 401）。

    回归场景：用户 shell 残留 DEEPSEEK_API_KEY=sk-…(DeepSeek)，但设置页把 base_url
    切到 LongCat 并保存了 ak_… key；若 env 优先，运行期会把 sk-… 发到 LongCat → 401
    invalid_api_key（无效的AppId: sk）。
    """
    from unittest.mock import patch
    saved = os.environ.get("DEEPSEEK_API_KEY")
    os.environ["DEEPSEEK_API_KEY"] = "sk-999999999999999999999999999999"
    try:
        with patch.object(llm_advisor, "_load_llm_config", return_value={
            "config": {
                "model": "LongCat-2.0",
                "base_url": "https://api.longcat.chat/openai",
                "api_key": "ak_CONFIG_KEY",
            }
        }):
            assert llm_advisor._api_key() == "ak_CONFIG_KEY"
    finally:
        if saved is None:
            os.environ.pop("DEEPSEEK_API_KEY", None)
        else:
            os.environ["DEEPSEEK_API_KEY"] = saved


def test_advise_real_if_key():
    """有 key 时真实调一次 LLM；无 key 则跳过（避免 CI/本地无凭证失败）。"""
    if not llm_advisor.has_api_key():
        print("  SKIP test_advise_real_if_key (无 DEEPSEEK_API_KEY)")
        return
    d = _scan({"monthly_rent": 8000, "monthly_revenue": 50000})
    out = advise(d, "开咖啡店，月租8000，月营收5万")
    # 现形签名：返回 {"text": str, "ops": list}；失联网兜底仍为 dict 不崩
    assert isinstance(out, dict)
    assert "text" in out and "ops" in out
    assert isinstance(out.get("ops", []), list)


def test_is_decision_scan():
    """识别 decision_engine 产物（与普通 scan 区分）。"""
    assert llm_advisor._is_decision_scan(
        {"type": "turnaround", "confidence": "full_user", "options": []}) is True
    assert llm_advisor._is_decision_scan(
        {"core_metrics": {"monthly_profit": -2100}}) is False
    assert llm_advisor._is_decision_scan(None) is False


# ─── 独立运行入口（无需 pytest）──────────────────────────────────────────

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
            print(f"ERROR {t.__name__}: {e}")
            failed += 1
    print(f"\n=== {passed} passed, {failed} failed ===")
    sys.exit(1 if failed else 0)
