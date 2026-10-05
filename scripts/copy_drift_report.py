#!/usr/bin/env python3
"""E-11 双副本 drift 护栏 —— 事故形态不变量 + 同源锚点 + 有意分化区登记。

背景：shangzhu-en（英文，本仓）与 shangzhu（中文）是同构双副本，2026-10-01 事故
「EN 修了、中文版没修」——修复无法自动同步，同一 bug 两个产品各犯一次。

方向镜像（与中文仓 M-09 版**相反**——本仓是 EN，是项目边界内对象）：
- EN 违反事故形态不变量 / EN 独有不变量 → **硬失败**（exit 1，进 make test）；
- 中文仓违反 11 条不变量 → 仅 DRIFT 警告（边界外对象，只读对比零写入）；
- **同源锚点任一侧失配 → 硬失败**：这是「中文仓改了、EN 没移植」的报警器，
  中文仓演进时 EN 测试红 = 提醒搬移植（归档/合并/跟进口径见改进计划 E-11）。

三区边界（计划第 7 节 E-11，2026-10-04 已定）：
① 同源比对区（默认比）：storage/、financial_calculator、decision_engine、
   field_model、session_state、source_tags（INFINITE_MARK 哨兵两仓同构）
   + web_server.py / app.js 的形态断言（即 11 条事故形态不变量）；
② 有意分化区（排除但逐条登记理由，防「借排除夹带修复不同步」）：
   src/i18n/**、rules/en.yaml、config/industry_templates.yaml（美元市场数值）、
   CONVERTED_MODULES 四模块（op_executor / advisor_formatter / param_guard /
   param_advisor，i18n 化改造后与中文仓**永远**不同）——引擎语义由 E-03 改造后的
   EN 用例守，drift 不碰；
③ EN 独有不变量（增量）：i18n 缺键上报存在、/i18n.js no-cache。

粒度纪律：只比形态不变量，**不比整文件 hash**（KEY_FILES hash 与顶层符号集差异
仅信息展示）。白名单方向 = 默认全比 + 例外显式登记（带理由）。

用法：.venv/bin/python3 scripts/copy_drift_report.py [--json]
中文仓定位：env SHANGZHU_ZH_ROOT > ../shangzhu > ../../shangzhu；
    不可达 → 跳过跨副本对比并注明（CI 只有 EN 检出属正常态）。
"""
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAMILY = os.path.dirname(ROOT)
EN = "shangzhu-en"
ZH = "shangzhu"

KEY_FILES = ["web_server.py", "src/web_static/app.js", "tests/conftest.py", "Makefile"]


def zh_root():
    """中文仓定位：显式 env 优先（缺失即硬错），否则候选目录探测（找不到返回 None）。"""
    env = os.environ.get("SHANGZHU_ZH_ROOT")
    if env:
        if not os.path.isdir(env):
            sys.exit(f"SHANGZHU_ZH_ROOT={env} 不是目录——显式指定的中文仓不可用，拒绝降级猜测")
        return env
    for cand in (os.path.join(FAMILY, ZH), os.path.join(os.path.dirname(FAMILY), ZH)):
        if os.path.isdir(cand):
            return cand
    return None


def _read(copy_dir, rel):
    try:
        with open(os.path.join(copy_dir, rel), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


# ── 事故形态不变量（11 条，骨架继承自中文仓 M-09；方向镜像见模块 docstring）────
def inv_no_hardcoded_asset_ver(copy_dir):
    """不变量1：静态资源版本号不得写死（?v=20260413a 事故形态）。

    只看真实资源引用行（href/src），避免文档/注释里引用历史版本号被误伤。
    """
    src = _read(copy_dir, "web_server.py") or ""
    m = re.findall(r'(?:href|src)="[^"]*\?v=(?!__APP_)[A-Za-z0-9]+', src)
    return (not m, f"发现写死的资源版本号: {m[:3]}" if m else "")


def inv_no_cache_middleware(copy_dir):
    """不变量2：/static 必须有 no-cache 中间件（旧标签页跑旧 JS 的帮凶）。"""
    src = _read(copy_dir, "web_server.py") or ""
    ok = "Cache-Control" in src and "no-cache" in src
    return (ok, "" if ok else "缺 no-cache 中间件")


def inv_no_param_named_t(copy_dir):
    """不变量3：禁止函数参数叫 t（翻译函数遮蔽事故形态）。"""
    src = _read(copy_dir, "src/web_static/app.js") or ""
    bad = [ln.strip() for ln in src.splitlines()
           if re.search(r"function\s+\w+\s*\(\s*t\s*[,)]", ln) or re.search(r"\(\s*t\s*,[^)]*\)\s*=>", ln)]
    return (not bad, f"参数命名 t: {bad[:2]}" if bad else "")


def inv_no_bare_switchtask(copy_dir):
    """不变量4：switchTask 调用必须 await/.catch（无痕失败形态）。"""
    src = _read(copy_dir, "src/web_static/app.js") or ""
    bad = []
    for ln in src.splitlines():
        s = ln.strip()
        if s.startswith("//") or re.match(r"^(async\s+)?function\s+switchTask\s*\(", s):
            continue
        if "switchTask(" in ln and "await switchTask" not in ln and ".catch(" not in ln and not s.startswith("return "):
            bad.append(s)
    return (not bad, f"裸 switchTask: {bad[:2]}" if bad else "")


def inv_health_build_fingerprint(copy_dir):
    """不变量5：/health 暴露构建指纹（static_ver/commit）。"""
    src = _read(copy_dir, "web_server.py") or ""
    ok = "static_ver" in src and "commit" in src
    return (ok, "" if ok else "/health 缺 static_ver/commit 版本握手字段")


def inv_client_log_endpoint(copy_dir):
    """不变量6：前端错误上报端点存在（静默失败可服务端观测）。"""
    src = _read(copy_dir, "web_server.py") or ""
    ok = "/client-log" in src
    return (ok, "" if ok else "缺 /client-log 上报端点")


def inv_test_isolation(copy_dir):
    """不变量7：测试必须有 conftest 隔离（测试写真实库事故形态）。"""
    ok = os.path.isfile(os.path.join(copy_dir, "tests", "conftest.py"))
    return (ok, "" if ok else "缺 tests/conftest.py 测试库隔离")


def inv_lint_gate(copy_dir):
    """不变量8：ESLint 门禁存在。"""
    ok = os.path.isfile(os.path.join(copy_dir, "eslint.config.mjs"))
    return (ok, "" if ok else "缺 eslint.config.mjs 前端门禁")


def inv_ci_gate(copy_dir):
    """不变量9：CI 工作流存在。"""
    ok = os.path.isfile(os.path.join(copy_dir, ".github", "workflows", "ci.yml"))
    return (ok, "" if ok else "缺 .github/workflows/ci.yml")


def inv_e2e_smoke(copy_dir):
    """不变量10：浏览器 e2e 冒烟存在（运行期 TypeError 盲区）。"""
    ok = os.path.isfile(os.path.join(copy_dir, "tests", "e2e", "smoke.mjs"))
    return (ok, "" if ok else "缺 tests/e2e/smoke.mjs 浏览器冒烟")


def inv_report_client_error(copy_dir):
    """不变量11：前端统一错误出口 reportClientError 存在。"""
    src = _read(copy_dir, "src/web_static/app.js") or ""
    ok = "function reportClientError(" in src
    return (ok, "" if ok else "缺 reportClientError 统一错误出口")


INVARIANTS = [
    ("no_hardcoded_asset_ver", inv_no_hardcoded_asset_ver),
    ("no_cache_middleware", inv_no_cache_middleware),
    ("no_param_named_t", inv_no_param_named_t),
    ("no_bare_switchtask", inv_no_bare_switchtask),
    ("health_build_fingerprint", inv_health_build_fingerprint),
    ("client_log_endpoint", inv_client_log_endpoint),
    ("test_isolation", inv_test_isolation),
    ("lint_gate", inv_lint_gate),
    ("ci_gate", inv_ci_gate),
    ("e2e_smoke", inv_e2e_smoke),
    ("report_client_error", inv_report_client_error),
]


# ── ③ EN 独有不变量（增量，只查 EN）─────────────────────────────────────────
def inv_i18n_missing_key_reporting(copy_dir):
    """EN 独有1：t() 缺键必须上报 + 显式标记（缺失不冒充）。"""
    src = _read(copy_dir, "src/web_static/app.js") or ""
    ok = "'[i18n:missing:'" in src and "reportClientError('i18n', 'i18n_missing_key'" in src
    return (ok, "" if ok else "缺 i18n 缺键上报/显式标记")


def inv_i18n_js_no_cache(copy_dir):
    """EN 独有2：/i18n.js 必须走 no-cache（旧标签页旧文案帮凶）。"""
    src = _read(copy_dir, "web_server.py") or ""
    ok = bool(re.search(r'path in \("/", "/i18n\.js"\)', src)) and "no-cache" in src
    return (ok, "" if ok else "/i18n.js 缺 no-cache")


EN_ONLY_INVARIANTS = [
    ("i18n_missing_key_reporting", inv_i18n_missing_key_reporting),
    ("i18n_js_no_cache", inv_i18n_js_no_cache),
]


# ── ① 同源锚点（跨副本同步契约；任一侧失配 → 硬失败）────────────────────────
# 只钉「关键形态」——函数/类/哨兵的存在性，不比整文件 hash。
# 中文仓演进改了这些形态 → 本测试红 = 提醒 EN 移植；EN 侧丢了这些形态同样红。
ANCHORS = [
    ("infinite_mark_sentinel", "跑道无限哨兵值两仓同构（历史共库数据兼容，值必须是「无限」）", {
        "shangzhu": [("src/tools/financial_calculator.py", r'"runway_months":\s*"无限"')],
        "shangzhu-en": [("src/source_tags.py", r'INFINITE_MARK\s*=\s*"无限"'),
                        ("src/tools/financial_calculator.py", r'\bINFINITE_MARK\b')],
    }),
    ("decision_engine_api", "引擎决策 API 形态（decide/render_decision）", {
        "both": [("src/decision_engine.py", r"def decide\("),
                 ("src/decision_engine.py", r"def render_decision\(")],
    }),
    ("financial_calc_api", "测算 API 形态（breakeven/runway/unit_economics）", {
        "both": [("src/tools/financial_calculator.py", r"def calculate_breakeven\("),
                 ("src/tools/financial_calculator.py", r"def calculate_runway\("),
                 ("src/tools/financial_calculator.py", r"def calculate_unit_economics\(")],
    }),
    ("session_state_api", "会话状态 API 形态（apply_turn/get_params_version/merge_params_guarded）", {
        "both": [("src/session_state.py", r"def apply_turn\("),
                 ("src/session_state.py", r"def get_params_version\("),
                 ("src/session_state.py", r"def merge_params_guarded\(")],
    }),
    # 有意分化（2026-10-06）：EN 把持久化从 PG 换成 SQLite 单文件，
    # 两仓**故意不再同构**。守卫改为「不变量等价、实现可分化」——
    # 两仓都必须有：内存档 + 一个持久化档 + get_store 工厂；
    # 但持久化档是哪一种由各仓自选，不像 infinite_mark_sentinel 那样钉死。
    # 这是 E-11 三区里的「有意分化区」：只比形态不变量，不比实现。
    ("store_degrade_chain", "存储形态：内存档 + 持久化档 + get_store（持久化实现两仓可分化）", {
        "shangzhu": [("src/storage/local_store.py", r"class MemoryStore\b"),
                     ("src/storage/local_store.py", r"class LocalFileStore\b"),
                     ("src/storage/local_store.py", r"class PostgresStore\b"),
                     ("src/storage/local_store.py", r"def get_store\(")],
        "shangzhu-en": [("src/storage/local_store.py", r"class MemoryStore\b"),
                        ("src/storage/local_store.py", r"class SqliteStore\b"),
                        ("src/storage/local_store.py", r"def get_store\(")],
    }),
    ("field_model_api", "字段模型 API 形态（derive/consistency_issues/field_label）", {
        "both": [("src/field_model.py", r"def derive\("),
                 ("src/field_model.py", r"def consistency_issues\("),
                 ("src/field_model.py", r"def field_label\(")],
    }),
]


def check_anchors(zh_dir, en_dir):
    """返回 {anchor_name: (ok, detail)}——任一侧任一形态失配即 ok=False。"""
    out = {}
    for name, desc, sides in ANCHORS:
        problems = []
        plan = []
        if "both" in sides:
            plan = [(ZH, zh_dir, sides["both"]), (EN, en_dir, sides["both"])]
        else:
            for copy_name, patterns in sides.items():
                plan.append((copy_name, zh_dir if copy_name == ZH else en_dir, patterns))
        for copy_name, base, patterns in plan:
            for rel, pat in patterns:
                src = _read(base, rel)
                if src is None:
                    problems.append(f"{copy_name}:{rel} 缺文件")
                elif not re.search(pat, src):
                    problems.append(f"{copy_name}:{rel} 缺形态 /{pat}/")
        out[name] = (not problems, f"{desc}——" + "；".join(problems) if problems else desc)
    return out


# ── ② 有意分化区（排除但逐条登记理由；防借排除夹带）──────────────────────────
DIFFERENTIATED = {
    "src/i18n/": "locale 资源天然分化（en.yaml/zh.yaml 两种语言，对称性由 test_i18n_guard 管）",
    "rules/en.yaml": "EN 规则包与 rules/zh.yaml 有意分化（包间对称由 test_rules_guard 管）",
    "config/industry_templates.yaml": "美元市场数值有意分化（人民币模板数值不适用 EN 部署）",
    "src/op_executor.py": "CONVERTED_MODULES：i18n 化改造后与中文仓永远不同（引擎语义由 EN 用例守）",
    "src/advisor/advisor_formatter.py": "CONVERTED_MODULES：i18n 化改造后与中文仓永远不同（引擎语义由 EN 用例守）",
    "src/param_guard.py": "CONVERTED_MODULES：i18n 化改造后与中文仓永远不同（引擎语义由 EN 用例守）",
    "src/tools/param_advisor.py": "CONVERTED_MODULES：i18n 化改造后与中文仓永远不同（引擎语义由 EN 用例守）",
}


def check_copy(copy_dir):
    """返回 {invariant_name: (ok, detail)}（11 条事故形态不变量）。"""
    return {name: fn(copy_dir) for name, fn in INVARIANTS}


def check_en_only(copy_dir):
    """返回 {en_only_name: (ok, detail)}（③ EN 独有不变量）。"""
    return {name: fn(copy_dir) for name, fn in EN_ONLY_INVARIANTS}


def _file_hash(copy_dir, rel):
    try:
        with open(os.path.join(copy_dir, rel), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:12]
    except OSError:
        return "missing"


def _top_symbols(copy_dir, rel):
    import ast
    src = _read(copy_dir, rel)
    if src is None:
        return set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    return {n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def main():
    as_json = "--json" in sys.argv
    zh_dir = zh_root()
    en_dir = ROOT

    en_res = check_copy(en_dir)
    en_only = check_en_only(en_dir)
    zh_res = check_copy(zh_dir) if zh_dir else None
    anchors = check_anchors(zh_dir, en_dir) if zh_dir else None

    en_viol = [(n, d) for n, (ok, d) in list(en_res.items()) + list(en_only.items()) if not ok]
    zh_viol = [(n, d) for n, (ok, d) in zh_res.items() if not ok] if zh_res else []
    anchor_bad = [(n, d) for n, (ok, d) in anchors.items() if not ok] if anchors else []

    if as_json:
        print(json.dumps({
            "en": en_res, "en_only": en_only, "zh": zh_res, "anchors": anchors,
            "differentiated": DIFFERENTIATED,
        }, ensure_ascii=False, indent=2))
    else:
        print("== 事故形态不变量（11 条；EN 违规→硬失败，中文仓违规→DRIFT 警告）==")
        print(f"{'不变量':<28} {EN:<12} {ZH:<12}")
        for name, _ in INVARIANTS:
            en_ok = "OK" if en_res[name][0] else "VIOLATED"
            zh_ok = ("OK" if zh_res[name][0] else "VIOLATED") if zh_res else "n/a"
            print(f"{name:<28} {en_ok:<12} {zh_ok:<12}")
        print("\n== ③ EN 独有不变量 ==")
        for name, (ok, detail) in en_only.items():
            print(f"  {'OK' if ok else 'VIOLATED':<10} {name}  {detail if not ok else ''}")
        print("\n== ① 同源锚点（任一侧失配 → 硬失败 = 移植提醒）==")
        if anchors:
            for name, (ok, detail) in anchors.items():
                print(f"  {'OK' if ok else 'DRIFT-RED':<10} {name}  {'' if ok else detail}")
        else:
            print("  中文仓不可达——跳过（CI 只有 EN 检出属正常态）")
        print("\n== ② 有意分化区（排除但登记理由）==")
        for path, reason in DIFFERENTIATED.items():
            print(f"  {path:<36} {reason}")
        print("\n== KEY_FILES hash（全文对比必然不同——locale 不同，仅信息展示）==")
        for rel in KEY_FILES:
            hs = [_file_hash(d, rel) for d in (en_dir, zh_dir)] if zh_dir else [_file_hash(en_dir, rel), "n/a"]
            print(f"  {rel:<28} {hs[0]} / {hs[1]}")
        if zh_dir:
            print("\n== 同源模块顶层符号集差异（仅信息展示，不判失败）==")
            for rel in ("src/decision_engine.py", "src/field_model.py", "src/session_state.py",
                        "src/tools/financial_calculator.py", "src/storage/local_store.py"):
                a, b = _top_symbols(zh_dir, rel), _top_symbols(en_dir, rel)
                if a or b:
                    print(f"  {rel:<36} 仅zh {sorted(a - b)}  仅en {sorted(b - a)}")

    if zh_viol:
        print(f"\n[DRIFT 警告] {ZH} 未满足事故形态不变量（边界外对象，仅报告不阻塞）：")
        for name, detail in zh_viol:
            print(f"  - {name}: {detail}")
    if anchor_bad:
        print(f"\n[同源锚点失配] 必须处置（移植或修订锚点）：")
        for name, detail in anchor_bad:
            print(f"  - {name}: {detail}")
    if en_viol:
        print(f"\n[违规] {EN} 未满足（必须修复）：")
        for name, detail in en_viol:
            print(f"  - {name}: {detail}")
        return 1
    if anchor_bad:
        return 1
    print(f"\n{EN} 全部不变量满足 ✓" + (f"（{ZH} drift 见上方警告）" if zh_viol or not zh_dir else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
