"""E-11 双副本 drift 护栏测试 —— 脚本不许 bitrot，EN 不许违规，负向形状必须被抓住。

- 11 条事故形态不变量：EN 全部必须满足（硬失败）；中文仓违规只 DRIFT 警告（边界外对象）；
- ③ EN 独有不变量（i18n 缺键上报 / /i18n.js no-cache）；
- ① 同源锚点：中文仓改同源层关键形态 → 本测试红（移植提醒）；中文仓不可达则跳过（CI 常态）；
- ② 有意分化区登记：排除必须带理由，防「借排除夹带修复不同步」；
- 负向形状（护栏自证，2026-10-04 计划 E-11 验收）：
  人为在 EN 删掉 /client-log → 护栏红；伪造中文仓锚点漂移 → 护栏红。
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "src"))

import copy_drift_report as cdr  # noqa: E402


def test_en_satisfies_all_incident_invariants():
    results = cdr.check_copy(cdr.ROOT)
    violated = {n: d for n, (ok, d) in results.items() if not ok}
    assert not violated, f"EN 违反事故形态不变量: {violated}"


def test_en_only_invariants():
    results = cdr.check_en_only(cdr.ROOT)
    violated = {n: d for n, (ok, d) in results.items() if not ok}
    assert not violated, f"EN 违反 EN 独有不变量: {violated}"


def test_differentiated_zone_registry_reasons():
    """② 有意分化区：每条排除必须登记理由；基线集合锁死——新增排除必须改本测试。"""
    for path, reason in cdr.DIFFERENTIATED.items():
        assert reason and len(reason) >= 8, f"分化区登记缺理由: {path!r} -> {reason!r}"
    expected = {
        "src/i18n/", "rules/en.yaml", "config/industry_templates.yaml",
        "src/op_executor.py", "src/advisor/advisor_formatter.py",
        "src/param_guard.py", "src/tools/param_advisor.py",
    }
    assert set(cdr.DIFFERENTIATED) == expected, (
        "有意分化区集合变了——新增排除等于扩大白名单，必须显式改本测试并登记理由"
    )


def test_cross_copy_anchors_isomorphic():
    """① 同源锚点任一侧失配 → 红（中文仓改了 EN 没移植的报警器）。"""
    zh_dir = cdr.zh_root()
    if zh_dir is None:
        import pytest
        pytest.skip("中文仓不可达（CI 只有 EN 检出）——跨副本对比跳过")
    results = cdr.check_anchors(zh_dir, cdr.ROOT)
    drifted = {n: d for n, (ok, d) in results.items() if not ok}
    assert not drifted, f"同源锚点失配（需移植或修订锚点）: {drifted}"


def test_drift_report_runs_clean():
    out = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "copy_drift_report.py")],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert out.returncode == 0, f"drift 报告 exit {out.returncode}:\n{out.stdout}{out.stderr}"
    for marker in ("事故形态不变量", "EN 独有不变量", "同源锚点", "有意分化区"):
        assert marker in out.stdout, f"报告缺栏目: {marker}"


def test_report_ci_mode_without_chinese_repo(monkeypatch):
    """CI 形态：中文仓不可达 → 跳过跨副本对比，exit 0（不得误红）。"""
    monkeypatch.setattr(cdr, "zh_root", lambda: None)
    assert cdr.main() == 0


def test_explicit_zh_root_missing_fails_loud(monkeypatch, tmp_path):
    """显式指定的中文仓不存在 → 硬错（缺失不冒充，不静默降级猜测）。"""
    monkeypatch.setenv("SHANGZHU_ZH_ROOT", str(tmp_path / "nope"))
    out = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "copy_drift_report.py")],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert out.returncode != 0
    assert "SHANGZHU_ZH_ROOT" in out.stderr


def test_negative_remove_client_log_is_caught(tmp_path):
    """负向验收：人为在 EN 删掉 /client-log → 护栏红。"""
    shutil.copy(os.path.join(ROOT, "web_server.py"), tmp_path / "web_server.py")
    src = (tmp_path / "web_server.py").read_text(encoding="utf-8")
    (tmp_path / "web_server.py").write_text(src.replace("/client-log", "/x-gone"), encoding="utf-8")
    ok, detail = cdr.inv_client_log_endpoint(str(tmp_path))
    assert not ok, "删掉 /client-log 后护栏竟然还绿——不变量 6 失效"
    assert "/client-log" in detail


def test_negative_zh_anchor_change_is_caught(tmp_path):
    """负向验收：伪造中文仓同源锚点漂移 → 护栏红。"""
    fake_zh = tmp_path / "zh"
    (fake_zh / "src" / "tools").mkdir(parents=True)
    # 只放一个不含哨兵形态的 financial_calculator——其余锚点缺文件同样会红
    (fake_zh / "src" / "tools" / "financial_calculator.py").write_text(
        "def calculate_runway(): pass\ndef calculate_breakeven(): pass\n"
        "def calculate_unit_economics(): pass\n", encoding="utf-8")
    results = cdr.check_anchors(str(fake_zh), cdr.ROOT)
    ok, detail = results["infinite_mark_sentinel"]
    assert not ok, "中文仓哨兵形态变了护栏竟然还绿——锚点报警器失效"
    assert "financial_calculator" in detail
