"""临时校验脚本：跑 report_generator 的双语产出，检查英文版是否残留中文。

不属于门禁，只用于人工核对。用法：
    .venv/bin/python scripts/_verify_report_bilingual.py
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

CJK = "零一二三四五六七八九十百千万亿甲乙丙丁"


def _has_cjk(s: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" or c in CJK for c in s)


CODE = r'''
import json, sys, os
sys.path.insert(0, "src")
os.environ["SHANGZHU_LOCALE"] = sys.argv[1]
from tools.workflow_engine import quick_scan
from tools.report_generator import (
    build_report_markdown, build_excel_sheets, generate_business_canvas_report,
)

params = {
    "industry": sys.argv[2],
    "total_investment": 100000,
    "monthly_rent": 10000,
    "employee_count": 2,
    "avg_salary": 5000,
    "price_per_unit": 15,
    "daily_traffic": 50,
    "variable_cost_ratio": 0.55,
    "description": sys.argv[3],
}
scan = json.loads(quick_scan.invoke({"params_json": json.dumps(params, ensure_ascii=False)}))
md = build_report_markdown(scan)
sheets = build_excel_sheets(scan)
canvas = generate_business_canvas_report.invoke({"canvas_json": json.dumps({
    "value_proposition": sys.argv[4],
    "customer_segments": "office workers nearby",
})})
print("===== MARKDOWN =====")
print(md)
print("===== SHEETS =====")
print(json.dumps(sheets, ensure_ascii=False, indent=1))
print("===== CANVAS =====")
print(canvas)
print("===== SENSITIVITY RAW =====")
print(json.dumps(scan.get("sensitivity", {}), ensure_ascii=False)[:600])
print("===== PITFALLS RAW =====")
print(json.dumps(scan.get("pitfalls", {}), ensure_ascii=False)[:600])
'''

for locale, industry, desc, vp in (
    ("zh", "餐饮", "开一家奶茶店，面向所有人，没有竞争", "好喝不贵"),
    ("en", "餐饮", "open a milk tea shop, for everyone, no competition", "cheap and tasty"),
):
    print("#" * 30, locale, "#" * 30)
    r = subprocess.run([sys.executable, "-c", CODE, locale, industry, desc, vp],
                       capture_output=True, text=True,
                       cwd=os.path.join(os.path.dirname(__file__), ".."))
    out = r.stdout
    if r.returncode != 0:
        print(r.stderr[-3000:])
        continue
    print(out)
    if locale == "en":
        bad = [ln for ln in out.splitlines() if _has_cjk(ln)]
        print(">>> CJK 残留行数:", len(bad))
        for ln in bad[:20]:
            print("    ", ln)
