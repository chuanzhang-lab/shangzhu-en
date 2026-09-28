"""P2-A 行为快照 oracle：把「重构前/后」的抽取结果固化成 JSON 用于逐字节比对。

用法：
    .venv/bin/python scripts/p2a_snapshot.py /tmp/p2a_before.json
    .venv/bin/python scripts/p2a_snapshot.py /tmp/p2a_after.json
    diff /tmp/p2a_before.json /tmp/p2a_after.json   # 必须为空

覆盖：15 个字段的中文表述 + 行业识别 + 13 类意图。
语料取自真实口语说法（含万/亿/成/%/区间/口语缩写），不是理想化句子。
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from router.param_extractor import extract_params, is_valid_project_input  # noqa: E402
from router.intent import detect_intent, detect_intent_safe  # noqa: E402

# 覆盖 15 个字段 + 行业 + 边缘写法
CORPUS = [
    # ── 基础开店描述（多字段同句）────────────────────────────────────────
    "我想开一家牛肉面店，租金每月8000，员工2人，人均月薪5000，客单价25，日均客流100",
    "开个咖啡店，月租1万2，总投资30万，请3个人，每人月薪4500",
    "奶茶店，房租8000一个月，装修和设备投了20万，日客流150人，客单价18",
    # ── 万/亿/中文数字/口语缩写 ──────────────────────────────────────────
    "租金一万二",
    "总投资二十万",
    "月租一万五",
    "启动资金一百万",
    "投入3万",
    "总投资1.5万",
    "月营收60万",
    # ── 百分比 / 成 / 比率 ───────────────────────────────────────────────
    "变动成本率35%",
    "变动成本率0.35",
    "成本率七成",
    "毛利率60%",
    "毛利率是0.6",
    # ── 人工相关（人数/薪资/人工总额）─────────────────────────────────────
    "员工5人，人均月薪6000",
    "请两个人，一个月工资8000",
    "人工成本每月1万",
    "3个员工，工资一共15000",
    # ── 客流 / 单价 别名 ─────────────────────────────────────────────────
    "每天来80个客人",
    "日流水3000",
    "客单价30元",
    "每人消费45块",
    "一天卖200单",
    # ── 其他成本项 ───────────────────────────────────────────────────────
    "水电费每月2000",
    "包装费一个月3000",
    "提成每月5000",
    "其他固定成本每月1500",
    "单位变动成本8元",
    "月支出一共5万",
    "月利润2万",
    # ── 年租 ─────────────────────────────────────────────────────────────
    "年租金12万",
    "房租一年96000",
    # ── 行业识别（含易混：宠物医院 / 服装加工 / 卖鞋）─────────────────────
    "开一家宠物美容店",
    "做宠物医院",
    "我做服装加工的",
    "卖鞋的店",
    "开个培训班",
    "做跨境电商",
    "搞SaaS工具",
    "开个羊肉汤店",
    # ── 改参数 / 意图 ────────────────────────────────────────────────────
    "把租金改成9000",
    "如果客流降到80会怎样",
    "帮我生成PDF报告",
    "导出Excel模型",
    "怎么才能扭亏为盈",
    "未来半年的趋势怎么样",
    "对比一下两个方案",
    "多久能回本",
    "现金流会不会断",
    "成本主要花在哪",
    "如果租金涨20%影响多大",
    "行业平均毛利率是多少",
    "帮我调研一下周边竞品",
    "你好",
    # ── 无效 / 边缘输入 ──────────────────────────────────────────────────
    "",
    "今天天气不错",
    "??",
]


def snapshot() -> dict:
    out = {"cases": []}
    for text in CORPUS:
        try:
            params = extract_params(text)
        except Exception as exc:  # 不允许有异常；有则记录
            params = {"__error__": f"{type(exc).__name__}: {exc}"}
        try:
            intent, score = detect_intent(text)
        except Exception as exc:
            intent, score = ("__error__", f"{type(exc).__name__}: {exc}")
        try:
            safe = detect_intent_safe(text)
        except Exception as exc:
            safe = f"__error__:{exc}"
        try:
            valid = is_valid_project_input(text)
        except Exception as exc:
            valid = f"__error__:{exc}"
        out["cases"].append(
            {
                "text": text,
                "params": params,
                "intent": intent,
                "score": round(score, 6) if isinstance(score, (int, float)) else score,
                "intent_safe": safe,
                "valid": valid,
            }
        )
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: p2a_snapshot.py <output.json>", file=sys.stderr)
        sys.exit(2)
    dest = sys.argv[1]
    data = snapshot()
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    n = len(data["cases"])
    errs = sum(1 for c in data["cases"] if "__error__" in json.dumps(c, ensure_ascii=False))
    print(f"snapshot -> {dest}: {n} cases, {errs} error-cases")
