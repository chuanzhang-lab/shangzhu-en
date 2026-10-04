"""验证 Direction 1+5 合并改动：参数分组 + 注意力过滤 + hypotheses 进视图。"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from session_state import (
    apply_turn_guarded, reset_state, to_llm_view,
    infer_focus_fields, record_accepted_hypothesis, PARAM_GROUPS
)


def test_accepted_hypotheses_in_view():
    """Direction 3: accepted_hypotheses 进入 to_llm_view。"""
    tid = "test-hyp-view"
    reset_state(tid)
    apply_turn_guarded(tid, {"industry": "餐饮"}, "", "餐饮")
    record_accepted_hypothesis(tid, "avg_salary", 3000, "餐饮")
    view = to_llm_view(tid)
    assert "accepted_hypotheses" in view
    assert view["accepted_hypotheses"].get("avg_salary") == {"value": 3000, "industry": "餐饮"}
    print("PASS test_accepted_hypotheses_in_view")


def test_backward_compat_no_focus():
    """向后兼容：不传 focus_fields 时行为与旧版一致。"""
    tid = "test-compat"
    reset_state(tid)
    apply_turn_guarded(tid, {"monthly_rent": 8000, "daily_traffic": 60}, "")
    view = to_llm_view(tid)
    # 旧字段都在
    assert "params" in view
    assert "industry" in view
    assert "turn" in view
    assert "last_changes" in view
    # 新字段也在（不破坏旧消费者）
    assert "grouped_params" in view
    assert "accepted_hypotheses" in view
    assert "params_summary" in view
    # params_summary 无 focus 时为空
    assert view["params_summary"] == ""
    print("PASS test_backward_compat_no_focus")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"FAIL {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR {t.__name__}: {e}")
            failed += 1
    print(f"\n=== {passed} passed, {failed} failed ===")
    sys.exit(1 if failed else 0)
