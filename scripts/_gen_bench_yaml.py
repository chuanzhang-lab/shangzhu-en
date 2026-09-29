"""一次性脚本：生成 industry.name.* / bench.* 两个 yaml 片段。

zh 的 bench 值**直接从 config/industry_templates.yaml 读出**再写入 zh.yaml，
保证「zh.yaml 的中文 == 配置里的中文」不是手抄的 —— 手抄就会漂。
之后由 tests/test_i18n_guard.py::test_bench_copy_matches_config 长期守住：
配置改了而 zh.yaml 没跟上 → 测试红。

生成后本脚本即完成使命（保留在仓库里是为了让下次加行业时能重跑 + 可审计）。
"""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "industry_templates.yaml"

# 行业数据键 → 英文展示名。键本身（餐饮/SaaS）**绝不能改**，改了就查不到模板。
EN_NAMES = {
    "餐饮": "Food & Beverage",
    "零售": "Retail",
    "SaaS": "SaaS",
    "教育": "Education",
    "电商": "E-commerce",
    "制造": "Manufacturing",
    "宠物": "Pet Services",
    "医疗": "Healthcare",
    "金融": "Finance",
    "内容": "Content & Media",
    "房地产": "Real Estate",
    "企业服务": "Enterprise Services",
}

# 英文版行业基准文案。这些是**内容**不是 UI 文案，故按行业逐个给。
# 与中文一一对应（traffic/margin/breakeven/warning 四项），
# test_i18n_guard 会断言 12 个行业在两边都齐，缺一个就红。
EN_BENCH = {
    "餐饮": {
        "traffic": "80-250 servings/day",
        "margin": "15-25%",
        "breakeven": "6-12 months",
        "warning": "A new store typically only reaches 40-50% of its target footfall in the first 3 months",
    },
    "零售": {
        "traffic": "50-150 customers/day",
        "margin": "10-18%",
        "breakeven": "8-18 months",
        "warning": "Inventory turnover is the core metric — keep it within 30 days",
    },
    "SaaS": {
        "traffic": "N/A (measured by MRR)",
        "margin": "Target 20-30% (early-stage losses are normal)",
        "breakeven": "18-36 months",
        "warning": "LTV/CAC should be > 3 and monthly churn < 5%",
    },
    "教育": {
        "traffic": "Class capacity × number of sessions",
        "margin": "20-35%",
        "breakeven": "6-15 months",
        "warning": "A school operating licence is required; compliance cost is high",
    },
    "电商": {
        "traffic": "By GMV / order volume",
        "margin": "5-15%",
        "breakeven": "3-12 months",
        "warning": "Traffic cost keeps rising — keep CAC within 20% of average order value",
    },
    "制造": {
        "traffic": "N/A",
        "margin": "8-15%",
        "breakeven": "12-24 months",
        "warning": "If the yield rate is below 95%, the project is not viable",
    },
    "宠物": {
        "traffic": "10-30 pets/day",
        "margin": "20-30%",
        "breakeven": "8-15 months",
        "warning": "A pet safety incident can trigger a large one-off payout",
    },
    "医疗": {
        "traffic": "20-80 visits/day",
        "margin": "15-30%",
        "breakeven": "12-24 months",
        "warning": "Licensing barriers are extremely high; compliance runs 10-15% of total cost",
    },
    "金融": {
        "traffic": "N/A",
        "margin": "25-40%",
        "breakeven": "12-24 months",
        "warning": "Regulatory compliance is the first lifeline; compliance cost is extremely high",
    },
    "内容": {
        "traffic": "By follower count / view count",
        "margin": "20-40% (highly volatile)",
        "breakeven": "6-18 months",
        "warning": "Platform algorithm dependency is a severe risk — distribute across several platforms",
    },
    "房地产": {
        "traffic": "N/A",
        "margin": "15-25%",
        "breakeven": "12-36 months",
        "warning": "Leverage risk: interest rate moves hit profit directly",
    },
    "企业服务": {
        "traffic": "N/A (by contract value)",
        "margin": "15-25%",
        "breakeven": "12-24 months",
        "warning": "Customer concentration risk: a single customer above 30% of revenue is high risk",
    },
}


def _q(s: str) -> str:
    """YAML 双引号标量转义。"""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def build(locale: str) -> str:
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    industries = list(cfg["industry_templates"].keys())

    missing = [k for k in industries if k not in EN_NAMES or k not in EN_BENCH]
    if missing:
        sys.exit(f"行业缺英文条目: {missing} —— 先补齐再生成，别让缺项静默回退中文")

    out = []
    out.append("# ─── 行业展示名 ─────────────────────────────────────────────")
    out.append("# 键是**数据键**（引擎靠它查 industry_templates），只映射展示名，键本身永不变。")
    out.append("industry:")
    out.append("  name:")
    for k in industries:
        val = k if locale == "zh" else EN_NAMES[k]
        out.append(f'    "{k}": {_q(val)}')

    out.append("")
    out.append("# ─── 行业基准（内容，非 UI 文案）─────────────────────────────")
    out.append("# zh 侧的值必须与 config/industry_templates.yaml 的 benchmark 逐字一致，")
    out.append("# 由 test_bench_copy_matches_config 守住；改配置不改这里 = 测试红。")
    out.append("bench:")
    if locale == "zh":
        out.append('  note: "以上为行业参考区间（人民币口径），非预测值"')
    else:
        out.append('  note: "Industry reference ranges (CNY basis), not a forecast"')
    out.append("  label:")
    if locale == "zh":
        out.append('    traffic_range: "日均客流"')
        out.append('    profit_margin: "典型利润率"')
        out.append('    breakeven: "典型回本周期"')
        out.append('    key_warning: "核心风险"')
    else:
        out.append('    traffic_range: "Daily traffic"')
        out.append('    profit_margin: "Typical profit margin"')
        out.append('    breakeven: "Typical break-even"')
        out.append('    key_warning: "Key risk"')
    for group in ("traffic", "margin", "breakeven", "warning"):
        out.append(f"  {group}:")
        for k in industries:
            if locale == "zh":
                val = cfg["industry_templates"][k]["benchmark"][
                    {
                        "traffic": "daily_traffic_range",
                        "margin": "typical_profit_margin",
                        "breakeven": "avg_breakeven_months",
                        "warning": "key_warning",
                    }[group]
                ]
            else:
                val = EN_BENCH[k][group]
            out.append(f'    "{k}": {_q(val)}')
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    for loc in ("zh", "en"):
        path = ROOT / "src" / "i18n" / f"{loc}.yaml"
        text = path.read_text(encoding="utf-8")
        if "\nindustry:\n" in text or text.startswith("industry:"):
            sys.exit(f"{path} 已含 industry: 段 —— 本脚本只应跑一次，请先手工处理")
        if not text.endswith("\n"):
            text += "\n"
        path.write_text(text + "\n" + build(loc), encoding="utf-8")
        print("appended ->", path)
