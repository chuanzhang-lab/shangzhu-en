"""
项目状态管理器 — 创业者工作台的项目数据层

职责：
- 记录当前项目的所有参数（总投资、租金、客流、客单价、行业等）
- 支持更新单个参数，自动保留其他参数
- 支持创建、读取、清空项目
- 解析用户自然语言输入，提取结构化参数

解析唯一真相源：
- 本模块不再维护独立的金额/整数/行业正则解析器（旧 _parse_amount /
  _parse_int / _infer_industry 与引擎层 router.param_extractor 重复，易造成
  双解析器语义漂移）。parse_project_params 现委托 router.param_extractor
  .extract_params 完成解析，仅做字段映射以兼容 B 路径 agent 的历史 schema。

用法：
  用户说"开咖啡馆投资50万月租15000"
  → create_or_update(text) 解析 → 返回当前项目状态
"""

import json
import re
from typing import Optional
from langchain.tools import tool

from i18n import industry_name, t

# 复用引擎层统一的参数提取器（唯一真相源），避免双解析器漂移
from router.param_extractor import extract_params as _extract_params


# ─── 解析器（委托给引擎层唯一真相源）─────────────────────────────────────


def parse_project_params(text: str) -> dict:
    """从用户自然语言中提取项目参数。

    委托 router.param_extractor.extract_params（唯一真相源）完成解析，
    并将输出字段映射到本项目历史 schema，保持对 B 路径 agent 的兼容：
    - founder_count 来自 employee_count（历史用「合伙人/创始人」计数，默认 1）
    - city 由轻量正则补充（引擎层 extract_params 不覆盖该私有字段）
    """
    raw = _extract_params(text)
    params: dict = {}

    # 直接对齐的字段
    for f in ("industry", "total_investment", "monthly_rent",
              "daily_traffic", "price_per_unit"):
        if f in raw and raw[f] is not None:
            params[f] = raw[f]

    # founder_count：历史用「合伙人/创始人」计数，默认 1
    if raw.get("employee_count") is not None:
        params["founder_count"] = int(raw["employee_count"])
    else:
        params["founder_count"] = 1

    # city：引擎层 extract_params 未覆盖，保留原有轻量正则
    city_match = re.search(r'在(\w+(?:市|区|县))', text)
    if city_match:
        params["city"] = city_match.group(1)

    return params


# ─── 工具 ──────────────────────────────────────────────────────────────────


@tool
def create_or_update_project(text: str) -> str:
    """
    创建或更新项目状态。解析用户自然语言中的参数，更新项目状态。

    参数:
        text: 用户输入的项目描述或参数修改指令

    返回: JSON 字符串，包含当前完整的项目状态
    """
    try:
        parsed = parse_project_params(text)
        # 返回当前项目状态（由系统记忆维护）
        return json.dumps({
            "action": "parsed",
            "params": parsed,
            "message": t("pm.extracted", n=len(parsed), fields=", ".join(parsed.keys())),
            "missing": _check_missing_params(parsed),
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": t("pm.parse_fail") + f": {str(e)}"}, ensure_ascii=False)


@tool
def update_project_param(key: str, value: str) -> str:
    """
    更新项目单个参数。

    参数:
        key: 参数名（如 total_investment, monthly_rent, daily_traffic, price_per_unit, industry, city）
        value: 参数值字符串

    返回: JSON 字符串，确认更新
    """
    return json.dumps({
        "action": "update",
        "key": key,
        "value": value,
        "message": t("pm.updated", field=key, value=value)
    }, ensure_ascii=False)


@tool
def get_project_summary(params_json: str) -> str:
    """
    生成当前项目的摘要。

    参数:
        params_json: 项目参数字典的 JSON 字符串

    返回: JSON 字符串，包含项目摘要和缺失的必填参数
    """
    try:
        params = json.loads(params_json) if isinstance(params_json, str) else params_json
        industry = params.get("industry")
        city = params.get("city")
        return json.dumps({
            "project_summary": f"{industry_name(industry) if industry else t('pm.unknown')} · {city or t('pm.unknown')}",
            "provided_params": list(params.keys()),
            "param_count": len(params),
            "missing_required": _check_missing_params(params),
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def _check_missing_params(params: dict) -> list:
    """检查哪些必要参数缺失"""
    required = [
        ("total_investment", t("field.label.total_investment")),
        ("monthly_rent", t("field.label.monthly_rent")),
        ("daily_traffic", t("field.label.daily_traffic")),
        ("price_per_unit", t("field.label.price_per_unit")),
    ]
    missing = []
    for key, label in required:
        if key not in params or params[key] is None:
            missing.append(label)
    return missing
