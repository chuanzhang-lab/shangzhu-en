#!/usr/bin/env python3
"""E-03 测试重分类标注脚本：对全量用例逐条打三标，产出重分类报告。

判据（计划 E-03）：**locale=en 的生产链路会不会走到被测代码**，而不是输入是什么语言。
- `zh-only`：zh 规则包专属抽取/路由、zh 文案渲染——en 部署不可达 → 批次 A 退役；
- `en-reachable`：引擎/web/存储契约——en 部署会走到 → 批次 B 换英文输入/断言保留；
- `language-neutral`：护栏/存储/观测位——与 locale 无关 → 原样保留。

标注来源分四层（报告每行标注 provenance）：
1. **钉子翻转探针**（docs/en-pin-probe-20261004.txt）：临时把 conftest 的
   SHANGZHU_LOCALE 钉子翻成 en 跑全量、抓红名单、立即回退。
   **探针绿 = en 钉下实证已绿 → 一律 language-neutral 原样保留**（不看先验不看信号，
   实证优先）；只有探针红的行才进入下面三层裁决。
2. M06 旧名单先验（docs/M06_test_disposition.md，一~七类）；
3. 源码信号（CJK 在输入还是断言、被测模块分类）；
4. 人工裁决表（CURATED_KEEP，计划点名必须保留的误分类 + 护栏文件）。

用法：
    .venv/bin/python3 scripts/reclassify_tests.py            # 生成报告到 docs/
    .venv/bin/python3 scripts/reclassify_tests.py --stdout   # 只打印不落盘
"""
import ast
import io
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.join(ROOT, "tests")
M06_DOC = os.path.join(ROOT, "docs", "M06_test_disposition.md")
PROBE_TXT = os.path.join(ROOT, "docs", "en-pin-probe-20261004.txt")
REPORT = os.path.join(ROOT, "docs", "test-reclassification-en-20261004.md")

# ── M06 旧名单的小节 → 三标先验 ─────────────────────────────────────────────
# 一~四（护栏/无中文/数据标记/数值断言）旧判「保留」→ language-neutral 先验；
# 五/六（中文抽取/纯中文文案）旧判「退役」→ zh-only 先验，但含误分类，逐条过信号；
# 七（待人工）→ 无先验，全靠信号 + 人工裁决。
M06_SECTIONS = {
    "一": ("护栏_安全_架构守护", "language-neutral"),
    "二": ("白_无中文_语言无关", "language-neutral"),
    "三": ("白_引擎数据标记", "language-neutral"),
    "四": ("白_数值断言_输入无中文", "language-neutral"),
    "五": ("退_中文抽取能力", "zh-only"),
    "六": ("退_纯中文文案", "zh-only"),
    "七": ("待定_需人工判断", None),
}

# ── 人工裁决表（provenance 含 curated）───────────────────────────────────────
# 计划 E-03 点名：旧「退役」名单里的这些**全部应保留**（误分类）：
#   test_appjs_translator_shadow、test_task_api、test_local_store、
#   test_web_server_robustness ~10 条 —— 护栏/API 契约/存储契约，删=拆安全网。
CURATED_KEEP = {
    "test_appjs_translator_shadow.py": "language-neutral",  # 事故护栏（2026-10-01）
    "test_task_api.py": "language-neutral",                 # API/存储契约
    "test_local_store.py": "language-neutral",              # 存储契约
    "test_web_server_robustness.py": "language-neutral",    # HTTP 层健壮性
    "test_persistence_guard.py": "language-neutral",
    "test_logger_namespace.py": "language-neutral",
    "test_rules_guard.py": "language-neutral",              # 中英规则包对称护栏
    "test_cjk_leak_guard.py": "language-neutral",           # EN 渲染出口零中文护栏
    "test_i18n_guard.py": "language-neutral",               # i18n 对称/依赖护栏
    "test_isolation_guard.py": "language-neutral",          # E-01 隔离护栏
    "test_static_cache_guard.py": "language-neutral",       # 缓存护栏（2026-10-03 事故）
    "test_config_priority.py": "language-neutral",
    "test_concurrency_fixes.py": "language-neutral",
    "test_cors_config.py": "language-neutral",
    "test_cleanup.py": "language-neutral",
    "test_client_log_contract.py": "language-neutral",      # E-06
    "test_health_version_fields.py": "language-neutral",     # E-07
    "test_no_unawaited_switchtask.py": "language-neutral",   # E-05
}

# 文件级被测对象分类（三标的关键维度：被测代码 en 部署是否可达）：
#   guard    —— 护栏/存储/观测位，与 locale 无关
#   zh-pack  —— zh 规则包专属抽取/路由（措辞矩阵/口语句/解析缺陷），en 不可达
#   shared   —— 引擎/web/存储/渲染等共享模块，en 部署会走到
#   en-native —— 本来就测英文链路
FILE_CLASS = {}
for _f in CURATED_KEEP:
    FILE_CLASS[_f] = "guard"
for _f in (
    "test_param_extractor_fixes.py",   # zh 解析缺陷（『员工』/『日均卖80碗』类措辞）
    "test_extractor_coverage.py",      # zh 措辞矩阵 oracle（gross_margin 单位契约部分除外）
    "test_pipeline_maturity.py",       # 旗舰中文口语句 → 抽取链路
    "test_vc_consistency.py",          # 『变动成本改为60%』类 zh 抽取
):
    FILE_CLASS[_f] = "zh-pack"
for _f in ("test_en_extraction.py", "test_en_extraction_gaps.py"):
    FILE_CLASS[_f] = "en-native"

# 混合文件（zh-pack 与 shared 用例同居），行级标 mixed 供人工逐条复核
MIXED_FILES = {
    "test_phase3_session_alignment.py", "test_phase4_workbench.py",
    "test_real_dialogs.py", "test_extractor_coverage.py",
    "test_pipeline_maturity.py", "test_vc_consistency.py",
    "test_hypothesis_layer.py", "test_direction_2_4.py", "test_direction_5_1.py",
}

# 文件级默认（en_* 本身就是 en 链路用例）
FILE_DEFAULTS = {
    "test_en_extraction.py": "en-reachable",
    "test_en_extraction_gaps.py": "en-reachable",
}

CJK_RE = re.compile(r"[一-鿿]")


def collect_tests():
    out = subprocess.check_output(
        [os.path.join(ROOT, ".venv", "bin", "python3"), "-m", "pytest",
         "tests/", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=ROOT, stderr=subprocess.DEVNULL, text=True,
    )
    nodeids = []
    for line in out.splitlines():
        line = line.strip()
        if "::" in line and line.startswith("tests/"):
            nodeids.append(line)
    return nodeids


def load_probe(nodeids):
    """读钉子翻转探针产物 → (红名单 nodeid 集合, failed, passed)。

    失败行是 pytest -rf 短摘要（80 列截断）：nodeid 可能被截断成前缀。
    匹配顺序：精确 → 前缀唯一 → 前缀展开（参数化）。三步都对不上就报错，
    绝不静默把红行当绿行（缺失不冒充）。
    """
    if not os.path.exists(PROBE_TXT):
        sys.exit(f"缺探针产物 {PROBE_TXT}：先跑钉子翻转探针（见报告「探针方法」）")

    red_raw, failed, passed, summary = [], None, None, ""
    for line in open(PROBE_TXT, encoding="utf-8", errors="replace"):
        if line.startswith("FAILED "):
            red_raw.append(line[7:].split(" - ")[0].rstrip())
        m = re.search(r"^(\d+) failed, (\d+) passed", line)
        if m:
            failed, passed, summary = int(m.group(1)), int(m.group(2)), line.strip()
    if failed is None:
        sys.exit("探针产物里没有汇总行（N failed, M passed）——产物不完整")
    if "skipped" in summary or "error" in summary:
        sys.exit(f"探针汇总含 skipped/error：{summary}——跳过/出错的用例无法当「绿」处理，需先补齐探针")
    if len(red_raw) != failed:
        sys.exit(f"探针 FAILED 行数 {len(red_raw)} ≠ 汇总 failed {failed}——解析或产物损坏")

    red = set()
    for r in red_raw:
        if r in nodeids:
            red.add(r)
            continue
        cands = [n for n in nodeids if n.startswith(r.rstrip("."))]
        if not cands:
            sys.exit(f"探针红行无法匹配到收集清单：{r!r}")
        red.update(cands)
    if len(red) != failed:
        sys.exit(f"红名单匹配后 {len(red)} ≠ failed {failed}（截断前缀命中多个参数化变体？）")
    return red, failed, passed


def parse_m06():
    """M06 文档 → {test_name(no file prefix): (section_key, old_category)}。"""
    mapping = {}
    section = None
    with open(M06_DOC, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^##\s*([一二三四五六七])、", line)
            if m:
                section = m.group(1)
                continue
            m = re.match(r"^-\s*(test_\S+?::\S+)", line)
            if m and section:
                node = m.group(1)
                name = node.split("::", 1)[1] if "::" in node else node
                mapping[name] = (section, M06_SECTIONS[section][1])
    return mapping


def load_test_source(path, qualname):
    """取测试函数源码（class::method 或 function）。"""
    with open(path, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    parts = qualname.split("::")
    node = tree
    for part in parts:
        found = None
        for child in ast.walk(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and child.name == part:
                found = child
                break
        if not found:
            return ""
        node = found
    return ast.get_source_segment(src, node) or ""


def signals(source):
    """源码信号：CJK 出现在断言里（zh 文案渲染）还是只在输入里（恰好是中文输入）。

    只看 AST 字符串常量——注释里的中文不算（本仓注释全中文，扫原文会全军覆没）；
    docstring 也不算（它是说明不是被测数据）。
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False, False

    docstring_nodes = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(n, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstring_nodes.add(id(body[0].value))

    anywhere = False
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) \
                and id(n) not in docstring_nodes and CJK_RE.search(n.value):
            anywhere = True
            break

    in_assert = False
    for n in ast.walk(tree):
        if isinstance(n, ast.Assert):
            for c in ast.walk(n):
                if isinstance(c, ast.Constant) and isinstance(c.value, str) \
                        and id(c) not in docstring_nodes and CJK_RE.search(c.value):
                    in_assert = True
                    break
    in_input = anywhere and not in_assert
    return in_assert, in_input


def label_for(file_name, qualname, m06, source, is_red):
    """返回 (label, provenance, note)。

    实证优先：**探针绿 → language-neutral**（en 钉下已经全绿，先验/信号都不再推翻）。
    探针红才进裁决，决策次序：
    1) curated/护栏文件 → en-reachable（护栏必须活在 en 钉下，换英文断言，绝不退役）；
    2) zh-pack 文件 → zh-only（zh 规则包专属能力）；
    3) M06 五/六退役先验 → zh-only（M06 退役裁决 + 探针红双证）；
    4) 其余（白/护栏/待定先验）→ en-reachable（安全不对称：宁可多留待改造）。
    """
    sec = m06.get(qualname) or m06.get(qualname.split("::")[-1])
    old_cat = M06_SECTIONS[sec[0]][0] if sec else "（M06 后新增）"
    mixed = "，mixed 文件需逐条复核" if file_name in MIXED_FILES else ""

    if not is_red:
        if file_name in CURATED_KEEP:
            return "language-neutral", "probe绿+curated", "计划点名保留/护栏"
        return "language-neutral", "probe绿", old_cat + mixed

    # ── 探针红 ──
    if file_name in CURATED_KEEP or file_name in FILE_DEFAULTS:
        return "en-reachable", "probe红+curated", "护栏/契约红→批次 B 换英文断言，不退役" + mixed
    cls = FILE_CLASS.get(file_name, "shared")
    if cls == "zh-pack":
        return "zh-only", "probe红+zh-pack", old_cat + mixed
    if sec and sec[0] in ("五", "六"):
        return "zh-only", "probe红+M06退役先验", old_cat + mixed
    return "en-reachable", "probe红+shared", old_cat + mixed


def main():
    to_stdout = "--stdout" in sys.argv
    m06 = parse_m06()
    nodeids = collect_tests()
    red_set, n_failed, n_passed = load_probe(nodeids)

    rows = []
    for nid in nodeids:
        path, _, rest = nid.partition("::")
        qual = rest.split("[")[0]  # 参数化后缀剥掉
        file_name = os.path.basename(path)
        try:
            source = load_test_source(os.path.join(ROOT, path), qual)
        except FileNotFoundError:
            source = ""
        is_red = nid in red_set
        label, prov, note = label_for(file_name, qual, m06, source, is_red)
        rows.append((nid, label, prov, note, "红" if is_red else "绿", source))

    counts = {}
    for _, label, _, _, _, _ in rows:
        counts[label] = counts.get(label, 0) + 1

    # 边界复核清单素材：探针红但默认判 en-reachable 的可疑行
    suspect = []  # (nid, sig, old_cat) —— shared 红 + 中文输入 or 无中文信号
    for nid, label, prov, note, color, source in rows:
        if label != "en-reachable" or not prov.startswith("probe红+shared"):
            continue
        in_assert, in_input = signals(source)
        if in_input or not (in_assert or in_input):
            suspect.append((nid, "input-CJK" if in_input else "no-CJK", note))

    buf = io.StringIO()
    buf.write("# E-03 测试重分类报告（2026-10-04）\n\n")
    buf.write("判据：**locale=en 的生产链路会不会走到被测代码**（不是输入是什么语言）。\n")
    buf.write("三标：`zh-only`（en 不可达 → 批次 A 退役）/ `en-reachable`（en 会走到 → "
             "批次 B 换英文输入/断言保留）/ `language-neutral`（护栏·存储·观测位 → 原样保留）。\n\n")
    buf.write("生成方式：`scripts/reclassify_tests.py`（钉子翻转探针 + M06 先验 + 源码信号 + "
             "人工裁决表）。**实证优先：探针绿的行一律原样保留**，只有探针红的行才进裁决。"
             "provenance 含 curated 为人工裁决，auto/probe 系脚本信号——**批次执行前需人工复核**，"
             "重点复核下方「边界复核清单」。\n\n")

    buf.write("## 探针方法（钉子翻转实测）\n\n")
    buf.write("把 `tests/conftest.py` 的 `SHANGZHU_LOCALE` 钉子临时翻成 `en` 跑全量 pytest，"
             f"抓红名单后**立即 git checkout 回退**（conftest 未入本 commit，diff 为空）。\n\n")
    buf.write(f"- 实测：**{n_failed} 红 / {n_passed} 绿**（合计 {n_failed + n_passed}，与收集数一致）\n")
    buf.write("- 产物：`docs/en-pin-probe-20261004.txt`（pytest -rf 短摘要，无连接串/密钥）\n")
    buf.write("- 语义：绿 = en 钉下已绿 → 不需要任何改造；红 = 钉子翻转前必须处置"
             "（改造或退役）——批次 A+B 的工作量以此为准，不以先验估数为准\n\n")

    buf.write("## 汇总\n\n")
    buf.write(f"| 三标 | 数量 | 处置 |\n|---|---|---|\n")
    buf.write(f"| zh-only | {counts.get('zh-only', 0)} | 批次 A 退役（删文件，靠 git history 恢复） |\n")
    buf.write(f"| en-reachable | {counts.get('en-reachable', 0)} | 批次 B 换英文输入/断言改造保留 |\n")
    buf.write(f"| language-neutral | {counts.get('language-neutral', 0)} | 原样保留（探针实证 en 钉已绿） |\n")
    buf.write(f"| **合计** | **{len(rows)}** | （M06 时点 512 → 现 {len(rows)}） |\n\n")
    buf.write(f"探针红 {n_failed} = zh-only {counts.get('zh-only', 0)} + "
             f"en-reachable {counts.get('en-reachable', 0)}（两标全部且仅有探针红行）；"
             f"探针绿 {n_passed} 全部 language-neutral。\n\n")

    buf.write("## 边界复核清单（人工必看）\n\n")
    buf.write("计划 E-03 点名的旧名单误分类——以下**全部应保留**，脚本已按 curated 裁决"
             "（其中 8 行探针红 → 判 en-reachable 批次 B 换英文断言，**不退役**）：\n\n")
    buf.write("- `test_appjs_translator_shadow`（事故护栏，2026-10-01 翻译函数遮蔽）\n")
    buf.write("- `test_task_api`（API/存储契约）\n")
    buf.write("- `test_local_store`（存储契约）\n")
    buf.write("- `test_web_server_robustness`（HTTP 健壮性）\n\n")
    buf.write(f"探针红但默认判 en-reachable 的可疑行（共 {len(suspect)} 行）——中文输入类"
             "（input-CJK）可能是 zh 包专属能力（EN 不承诺中文输入），若认定应退役可改判 "
             "zh-only 移入批次 A；no-CJK 类是无中文信号却红（数据在夹具/名称/金样文件），"
             "批次 B 改造时先看失败信息再动手：\n\n")
    for nid, sig, note in suspect:
        buf.write(f"- `{nid}` [{sig}]（M06：{note.split('，mixed')[0]}）\n")
    buf.write("\n")

    buf.write("## 人工复核记录\n\n")
    buf.write("| 裁决 | 内容 | 依据 |\n|---|---|---|\n")
    buf.write("| 实证优先 | 探针绿 → language-neutral 原样保留，先验/信号不推翻 | "
             "en 钉下已绿是硬证据；反向推翻会无谓扩大批次 B |\n")
    buf.write("| 保留（curated） | 计划点名 4 组（translator_shadow / task_api / "
             "local_store / web_server_robustness） | 误分类——护栏与 API/存储契约，删=拆安全网；"
             "探针红的行只改造不退役 |\n")
    buf.write("| 保留（curated） | 14 个护栏/观测位文件整文件 | 事故形态护栏（CJK 泄漏/缓存/"
             "规则对称/隔离/编排），与 locale 无关 |\n")
    buf.write("| zh-only 双证 | zh-pack 文件红行，或 M06 五/六退役先验 + 探针红 | "
             "zh 规则包专属能力 en 不可达；M06 已有退役裁决 + 探针实证红，改判保留需人工推翻双证 |\n")
    buf.write("| 安全性不对称 | 其余探针红一律 en-reachable（宁可多留待改造），不可误删 | "
             "退役即删文件不可逆（计划 E-03：靠 git history 恢复）；批次 B 改造可增量做 |\n")
    buf.write("| mixed 文件 | phase3/phase4/real_dialogs/extractor_coverage/pipeline_maturity/"
             "vc_consistency/hypothesis_layer/direction_* 标 mixed | zh-pack 与 shared 用例同居，"
             "**批次 A 前逐条人工复核** |\n\n")
    buf.write("批次 A（退役）执行前，本表需用户逐行复核确认；批次 B（改造）与钉子 zh→en "
             "在 A/B 全部完成后执行（届时 test_i18n_guard 依赖钉 zh 的用例同步适配）。"
             "**用户复核确认为执行前置条件**。\n\n")

    buf.write("## 逐条标注（按文件分组）\n\n")
    cur_file = None
    for nid, label, prov, note, color, _src in rows:
        f = nid.split("::", 1)[0]
        if f != cur_file:
            cur_file = f
            buf.write(f"\n### `{f}`\n\n")
            buf.write("| 用例 | 三标 | 探针 | 依据 | M06 旧类 |\n|---|---|---|---|---|\n")
        qual = nid.split("::", 1)[1] if "::" in nid else nid
        buf.write(f"| `{qual}` | {label} | {color} | {prov} | {note} |\n")

    text = buf.getvalue()
    if to_stdout:
        print(text)
    else:
        with open(REPORT, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"报告已写入 {REPORT}")
        print("汇总:", counts, f"探针: {n_failed}红/{n_passed}绿", f"边界可疑行: {len(suspect)}")


if __name__ == "__main__":
    main()
