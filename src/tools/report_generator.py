"""
报告生成工具 — PDF/Excel 输出（本地生成）

全部本地生成：Excel 走 openpyxl，PDF/DOCX 降级保存为 Markdown 文件。
"""

import json
import os
import time
import tempfile
from typing import Optional
from langchain.tools import tool

from i18n import has, industry_display, industry_name, t
from source_tags import INFINITE_MARK

# 「无限」是引擎的**数据标记**（靠等值比较识别，见 financial_calculator._calc_runway、
# decision_engine、workflow_engine 的 `== INFINITE_MARK`），不是文案。渲染时必须映射成
# 展示文案，否则英文版报表会直接漏出中文（M-07 护栏实测抓到的泄漏）。
# 哨兵的唯一出处是 source_tags.INFINITE_MARK（历史协议值，不能改）；本别名只为
# 保留本文件既有的可读称呼，两处齐动才不会漂移。
_ENGINE_INFINITE_MARK = INFINITE_MARK

# output/ 目录文件数上限与 TTL，防止长期运行磁盘无限增长
_MAX_OUTPUT_FILES = 50
_OUTPUT_FILE_TTL_SECONDS = 3600 * 24  # 24 小时

# 尝试导入 openpyxl（本地 Excel 生成）
try:
    import openpyxl
    _HAS_OPENPYXL = True
except ImportError:
    _HAS_OPENPYXL = False


def _cleanup_output_dir(output_dir: str) -> None:
    """清理 output/ 目录：删除过期文件 + 限制文件总数上限。"""
    if not os.path.isdir(output_dir):
        return
    now = time.time()
    files = []
    for f in os.listdir(output_dir):
        fp = os.path.join(output_dir, f)
        if os.path.isfile(fp):
            try:
                mtime = os.path.getmtime(fp)
                if now - mtime > _OUTPUT_FILE_TTL_SECONDS:
                    os.remove(fp)
                else:
                    files.append((mtime, fp))
            except OSError:
                pass
    # 按修改时间倒序排列，只保留最新 _MAX_OUTPUT_FILES 个
    if len(files) > _MAX_OUTPUT_FILES:
        files.sort(key=lambda x: x[0], reverse=True)
        for _, fp in files[_MAX_OUTPUT_FILES:]:
            try:
                os.remove(fp)
            except OSError:
                pass


def _generate_excel_url(data: list[dict], title: str, sheet_name: str = "Sheet1") -> str:
    """生成 Excel 文件并返回路径"""
    if not _HAS_OPENPYXL:
        raise RuntimeError(t("rg.err.openpyxl_missing"))

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name

    if data:
        # 写入表头
        headers = list(data[0].keys())
        for col, header in enumerate(headers, 1):
            ws.cell(row=1, column=col, value=header)

        # 写入数据
        for row_idx, row_data in enumerate(data, 2):
            for col_idx, header in enumerate(headers, 1):
                ws.cell(row=row_idx, column=col_idx, value=row_data.get(header, ""))

    output_dir = os.path.join(os.path.dirname(__file__), "..", "..", "output")
    os.makedirs(output_dir, exist_ok=True)
    _cleanup_output_dir(output_dir)  # R2 修复：写文件前先清理过期/超限文件
    output_path = os.path.join(output_dir, f"{title}.xlsx")
    wb.save(output_path)
    return f"file://{os.path.abspath(output_path)}"


def _generate_pdf_url(markdown_content: str, title: str) -> str:
    """生成报告文件，本地保存为 Markdown"""
    output_dir = os.path.join(os.path.dirname(__file__), "..", "..", "output")
    os.makedirs(output_dir, exist_ok=True)
    _cleanup_output_dir(output_dir)  # R2 修复：写文件前先清理过期/超限文件
    output_path = os.path.join(output_dir, f"{title}.md")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(markdown_content)
    return f"file://{os.path.abspath(output_path)}"


def _generate_docx_url(markdown_content: str, title: str) -> str:
    """生成 DOCX 报告，本地保存为 Markdown"""
    output_dir = os.path.join(os.path.dirname(__file__), "..", "..", "output")
    os.makedirs(output_dir, exist_ok=True)
    _cleanup_output_dir(output_dir)  # R2 修复：写文件前先清理过期/超限文件
    output_path = os.path.join(output_dir, f"{title}.md")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(markdown_content)
    return f"file://{os.path.abspath(output_path)}"


@tool
def generate_financial_report(
    report_content_markdown: str,
    report_title: str = "financial_report",
) -> str:
    """
    生成财务分析报告（PDF 格式）。

    参数:
        report_content_markdown: Markdown 格式的报告内容
        report_title: 报告标题（仅英文字母/数字/下划线，用于文件名）

    返回: JSON 字符串，包含 PDF 下载 URL
    """
    try:
        safe_title = "".join(c for c in report_title if c.isalnum() or c == "_")[:50] or "financial_report"

        url = _generate_pdf_url(report_content_markdown, safe_title)

        return json.dumps({
            "success": True,
            "format": t("rg.fmt.markdown_local"),
            "title": safe_title,
            "download_url": url,
            "note": t("rg.note.saved_md")
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({
            "success": False,
            "error": t("rg.err.report_failed", err=e),
            "suggestion": t("rg.err.markdown_invalid")
        }, ensure_ascii=False)


@tool
def generate_financial_excel(
    sheets_json: str,
    report_title: str = "financial_model",
) -> str:
    """
    生成财务模型 Excel 文件。

    参数:
        sheets_json: JSON 字符串，每个元素为 {"sheet_name": "...", "data": [...]}
                     其中 data 为字典列表或二维列表
        report_title: 报告标题（仅英文字母/数字/下划线，用于文件名）

    返回: JSON 字符串，包含 Excel 下载 URL
    """
    try:
        sheets = json.loads(sheets_json)
        if not sheets:
            return json.dumps({"error": t("rg.err.sheets_empty")}, ensure_ascii=False)

        safe_title = "".join(c for c in report_title if c.isalnum() or c == "_")[:50] or "financial_model"

        first_sheet = sheets[0]
        data = first_sheet.get("data", [])
        sheet_name = first_sheet.get("sheet_name", "Sheet1")

        url = _generate_excel_url(data, safe_title, sheet_name)

        return json.dumps({
            "success": True,
            "format": "XLSX",
            "title": safe_title,
            "sheet_count": len(sheets),
            "download_url": url,
            "note": t("rg.note.excel")
        }, ensure_ascii=False, indent=2)

    except json.JSONDecodeError:
        return json.dumps({"error": t("rg.err.sheets_json_format")}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": t("rg.err.excel_failed", err=e)
        }, ensure_ascii=False)


@tool
def generate_business_canvas_report(
    canvas_json: str,
    report_title: str = "business_canvas",
) -> str:
    """
    生成商业模式画布报告（PDF 格式）。

    参数:
        canvas_json: JSON 字符串，包含画布 9 大模块
        report_title: 报告标题（仅英文）

    返回: JSON 字符串，包含 PDF 下载 URL
    """
    try:
        canvas = json.loads(canvas_json)

        # 键是**字段标签**（展示文案），值缺失时的占位也是文案 —— 两处都要走 i18n
        not_filled = t("rg.canvas.not_filled")
        fields = {
            t("rg.canvas.field.value_proposition"): canvas.get("value_proposition", not_filled),
            t("rg.canvas.field.customer_segments"): canvas.get("customer_segments", not_filled),
            t("rg.canvas.field.channels"): canvas.get("channels", not_filled),
            t("rg.canvas.field.customer_relationships"): canvas.get("customer_relationships", not_filled),
            t("rg.canvas.field.revenue_streams"): canvas.get("revenue_streams", not_filled),
            t("rg.canvas.field.key_resources"): canvas.get("key_resources", not_filled),
            t("rg.canvas.field.key_activities"): canvas.get("key_activities", not_filled),
            t("rg.canvas.field.key_partners"): canvas.get("key_partners", not_filled),
            t("rg.canvas.field.cost_structure"): canvas.get("cost_structure", not_filled),
        }

        md = t("rg.canvas.head", title=report_title)
        for k, v in fields.items():
            md += f"| **{k}** | {v} |\n"

        md += t("rg.canvas.consistency")

        safe_title = "".join(c for c in report_title if c.isalnum() or c == "_")[:50] or "business_canvas"

        url = _generate_pdf_url(md, safe_title)

        return json.dumps({
            "success": True,
            "format": t("rg.fmt.markdown_local"),
            "title": safe_title,
            "download_url": url,
            "fields_count": sum(1 for v in fields.values() if v != not_filled),
            "total_fields": 9,
            "note": t("rg.note.saved_md")
        }, ensure_ascii=False, indent=2)

    except json.JSONDecodeError:
        return json.dumps({"error": t("rg.err.canvas_json_format")}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": t("rg.err.canvas_failed", err=e)
        }, ensure_ascii=False)


def _display(key: str, prefix: str) -> str:
    """引擎**数据键** → 展示名（monthly_revenue → Monthly revenue）。

    报告是直接交到用户手上的文件：把 snake_case 数据键原样印出来等于把
    内部结构泄漏给读者。未知键回退成原键（可见地退化），而不是静默消失。
    """
    k = f"{prefix}.{key}"
    return t(k) if has(k) else key


def _cell(v) -> str:
    """报告单元格：**缺失**必须显式成「—」，绝不把 None 印成字面量 "None"。

    None 直出会被读者当成「数值就是 None」，与项目「缺失不冒充 0」同属一类事故。
    「无限」这类引擎数据标记必须映射成展示文案（否则英文版漏中文）。
    """
    if v is None:
        return "—"
    if str(v) == _ENGINE_INFINITE_MARK:
        return t("fmt.common.infinite")
    return str(v)


def _labeled_scenarios(scan: dict) -> list:
    """只取**带标签**的三档情景（悲观/中性/乐观）。

    _calc_sensitivity 返回的是 steps×steps 的完整网格（默认 3×3=9 条），
    只有 3 条带 `scenario` 标签。按索引切片（如 [:3]）取到的是网格前几条
    ——标签恰好落在索引 2/4/6 —— 于是表格里会出现「?」行和重复的无标签行。
    按标签筛才与 steps 参数解耦。
    """
    return [s for s in scan.get("sensitivity", {}).get("scenarios", []) if s.get("scenario")]


def build_report_markdown(scan: dict, title: Optional[str] = None) -> str:
    """把 quick_scan 结果整理成 Markdown 报告正文。"""
    # 默认标题不能在签名里写死成文案：默认参数在 import 时求值，会把语言冻结
    title = title or t("rg.report.default_title")
    lines = [f"# {title}", ""]
    # 幂等映射：引擎出口通常已映射成展示名，但存档 scan 里可能仍是数据键
    # （如「餐饮」）—— 直接不映射会让英文报告印出中文键，套 industry_name
    # 又会对已映射值刷缺键 ERROR。industry_display 两者都对。
    pt = industry_display(scan.get("project_type")) or t("rg.report.unknown")
    lines.append(t("rg.report.project_type", v=pt))
    lines.append(t("rg.report.stage", v=scan.get("stage") or t("rg.report.unknown")))
    lines.append("")

    core = scan.get("core_metrics", {})
    lines.append(t("rg.report.core_header"))
    lines.append(t("rg.report.core_table"))
    lines.append("|------|------|")
    for k, v in core.items():
        lines.append(f"| {_display(k, 'rg.metric')} | {_cell(v)} |")
    lines.append("")

    status = scan.get("status", {})
    if status:
        lines.append(t("rg.report.status_header"))
        for k, v in status.items():
            lines.append(f"- {_display(k, 'rg.status')}: {_cell(v)}")
        lines.append("")

    if scan.get("narrative"):
        lines.append(t("rg.report.narrative_header"))
        lines.append(f"> {scan['narrative']}")
        lines.append("")

    sens = _labeled_scenarios(scan)
    if sens:
        lines.append(t("rg.report.sens_header"))
        lines.append(t("rg.report.sens_table"))
        lines.append("|------|--------|")
        for s in sens:
            # 情景名来自引擎（fc.sens.*），已是展示文案；profit 是数值
            profit = s.get("profit")
            lines.append(f"| {s['scenario']} | {_cell(profit)} |")
        lines.append("")

    pits = scan.get("pitfalls", {}).get("pitfalls", [])
    if pits:
        lines.append(t("rg.report.pit_header"))
        for p in pits:
            lines.append(f"- {p.get('level', '')} {p.get('message', '')}")
        lines.append("")

    return "\n".join(lines)


def build_excel_sheets(scan: dict) -> list:
    """把 quick_scan 结果整理成 Excel sheet 结构。"""
    core_rows = []
    for k, v in scan.get("core_metrics", {}).items():
        core_rows.append({
            t("rg.sheet.core_col_metric"): _display(k, "rg.metric"),
            t("rg.sheet.core_col_value"): v,
        })

    status_rows = []
    for k, v in scan.get("status", {}).items():
        status_rows.append({
            t("rg.sheet.status_col_dim"): _display(k, "rg.status"),
            t("rg.sheet.status_col_eval"): v,
        })

    pit_rows = []
    for p in scan.get("pitfalls", {}).get("pitfalls", []):
        pit_rows.append({
            t("rg.sheet.pit_col_level"): p.get("level", ""),
            t("rg.sheet.pit_col_risk"): p.get("message", ""),
        })

    return [
        {"sheet_name": t("rg.sheet.core"), "data": core_rows},
        {"sheet_name": t("rg.sheet.status"), "data": status_rows},
        {"sheet_name": t("rg.sheet.pitfalls"), "data": pit_rows},
    ]
