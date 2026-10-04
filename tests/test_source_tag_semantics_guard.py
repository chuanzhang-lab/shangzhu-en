"""来源标记语义护栏（E-03 事故家族）——**展示文案绝不能当语义判据**。

2026-10-05 实测事故：en 部署下 `source_tags.mark()` 产出的是
"[Missing] / [User] / [Candidate]…"，而五处代码拿**中文展示串**
（`startswith("[缺失]")` 之类）做**语义判定**，五个分支在英文下全部静默落空：

| # | 位置 | en 下的静默后果 |
|---|---|---|
| 1 | `param_guard.classify_basis` | 缺失字段被标成 `user` —— 缺失冒充用户事实 |
| 2 | `decision_engine.build_evidence` gaps | 缺口清单恒空 —— 缺失被藏起来 |
| 3 | `router/formatter` 缺失字段跳过 | 缺字段当成不缺 |
| 4 | `router/formatter` cash 状态判定 | 告诉用户去补总投资（真实原因是缺变动成本率） |
| 5 | `web_server._build_advise_context` | `missing_params` 恒空 —— LLM 不再追问缺失参数 |

共同根因：`source_tags` 早已把「状态码（机器读）/ 展示文案（人读）」拆开，
但这五处没跟上，仍把本地化后的文案当状态码用。

本护栏在 **en 与 zh 双语言**下验证这些判定结果一致且非空/不冒充；
真正的兜底门是 `test_i18n_guard::test_no_hardcoded_cjk_in_converted_modules`
（2026-10-05 起，这四个中文字面量已从白名单移除，再出现即红）。
"""
import os
import sys

import i18n

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

import source_tags  # noqa: E402


def _each_locale():
    """在每种受支持语言下跑一遍同一个断言体。"""
    for loc in i18n.SUPPORTED_LOCALES:
        i18n.set_locale(loc)
        try:
            yield loc
        finally:
            i18n.reset_locale()


def _scan_with(missing_field="variable_cost_ratio"):
    """构造一份含「缺失字段」的 scan（展示串用当前语言的 mark 生成）。"""
    ps = {
        "monthly_rent": source_tags.mark(source_tags.USER) + " 5000",
        missing_field: source_tags.mark(source_tags.MISSING) + " not provided",
        source_tags.CODES_KEY: {"monthly_rent": source_tags.USER,
                                missing_field: source_tags.MISSING},
    }
    return {"param_sources": ps, "core_metrics": {}}


def test_missing_source_is_recognized_in_every_locale():
    """展示串 → 状态码的反查必须双语都能解出（否则后续判定全线落空）。"""
    for loc in _each_locale():
        for code in (source_tags.USER, source_tags.MISSING, source_tags.CANDIDATE,
                     source_tags.DERIVED, source_tags.CONFLICT, source_tags.INCOMPLETE):
            got = source_tags.code_of_display(source_tags.mark(code))
            assert got == code, (
                f"[{loc}] 展示串 {source_tags.mark(code)!r} 反查状态码得到 {got!r}，"
                f"期望 {code!r} —— 展示文案与状态码的映射断了"
            )
        # 历史存档里的中文标记也要能解（协议常量，跨语言兼容）
        assert source_tags.code_of_display("[缺失] 未提供") == source_tags.MISSING


def test_basis_classification_is_locale_independent():
    """basis 判定必须走状态码，换语言结果不许变（#1 的护栏）。

    事故形态：`classify_basis` 曾靠 `startswith("[用户]"/"[缺失]"/"[候选]")` 判定，
    en 下六条比对全部落空 → 一律落到旧代码末尾 `return "user"` 兜底
    → **缺失字段被标成 user**（缺失冒充用户事实，是「缺失不冒充」的反面）。
    """
    from param_guard import classify_basis, derive_basis_map

    expected = {
        source_tags.USER: "user",
        source_tags.DERIVED: "user",          # 由用户基础值推导
        source_tags.CONFLICT: "user",         # 用户给出但矛盾：仍是输入事实
        source_tags.INCOMPLETE: "user",       # 值算出来了但缺组件 → 基础仍是输入
        source_tags.CANDIDATE: "hypothesis",
        source_tags.MISSING: "missing",
    }
    for loc in _each_locale():
        for code, want in expected.items():
            got = classify_basis(source_tags.mark(code))
            assert got == want, (
                f"[{loc}] {code} → basis={got!r}，期望 {want!r}；"
                f"展示文案是 {source_tags.mark(code)!r}（判据又误用了展示串？）"
            )
        # 未知 / 空标记：宁可 missing，绝不冒充 user
        assert classify_basis("[???] 未知来源") == "missing"
        assert classify_basis("") == "missing"
        # _codes 通道与展示串通道结果一致
        src = {"f_missing": source_tags.mark(source_tags.MISSING),
               "f_user": source_tags.mark(source_tags.USER),
               source_tags.CODES_KEY: {"f_missing": source_tags.MISSING,
                                       "f_user": source_tags.USER}}
        assert derive_basis_map(src) == {"f_missing": "missing", "f_user": "user"}


def test_decision_engine_gaps_are_not_silently_empty():
    """gaps 必须在双语言下都识别出缺失字段（en 下曾恒空）。"""
    from decision_engine import build_evidence

    for loc in _each_locale():
        scan = _scan_with()
        gaps = build_evidence(scan).get("gaps") or []
        assert "variable_cost_ratio" in gaps, (
            f"[{loc}] 缺失字段没进 gaps（实为 {gaps}）——缺口被藏起来了"
        )
        assert "monthly_rent" not in gaps, (
            f"[{loc}] 用户给出的字段被误判为缺失：{gaps}"
        )


def test_cash_unknown_vc_branch_hits_in_every_locale():
    """缺变动成本率时，现金状态判定必须命中（en 下曾落去「需总投资」误导用户）。"""
    from router.formatter import _is_unknown_vc

    for loc in _each_locale():
        # 当前语言的「缺 VC」状态串必须被识别
        assert _is_unknown_vc(i18n.t("wf.status.cash_unknown_vc")) is True
        # 其他状态串不得误命中
        assert _is_unknown_vc(i18n.t("wf.status.cash_unknown_invest")) is False
        assert _is_unknown_vc(i18n.t("wf.status.cash_safe")) is False
        assert _is_unknown_vc("") is False
        # 历史存档里的中文状态串仍要认得（数据与 locale 不同语言的极端情形）
        assert _is_unknown_vc("⚪ 未知（需变动成本率）") is True
