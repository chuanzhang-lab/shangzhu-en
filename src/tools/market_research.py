"""
联网搜索工具 — 市场调研与竞品分析（本地工作台版）

本仓为本地独立工作台，未内置联网搜索服务。
三个搜索工具保留原有函数签名与返回结构，
当前统一返回「未启用」提示，供调用方按既有协议降级展示。
"""

import json
from langchain.tools import tool
from i18n import t


def _unavailable_response(query: str, tool_name: str) -> str:
    """联网搜索未启用时的统一返回"""
    return json.dumps({
        "error": t("mr.search_disabled"),
        "query": query,
        "tool": tool_name,
        "suggestion": t("mr.use_benchmark_hint")
    }, ensure_ascii=False, indent=2)


@tool
def search_market_data(query: str, count: int = 5) -> str:
    """
    联网搜索市场数据、行业报告、竞品信息。

    参数:
        query: 搜索关键词，如 "2024年中国咖啡市场规模" 或 "SaaS行业平均获客成本"
        count: 返回结果数量，默认 5

    返回: JSON 字符串，包含搜索结果摘要
    """
    return _unavailable_response(query, "search_market_data")


@tool
def search_competitor_info(company_or_product: str, industry: str = "") -> str:
    """
    搜索竞争对手信息。

    参数:
        company_or_product: 竞品公司名或产品名
        industry: 所属行业（可选，帮助缩小范围）

    返回: JSON 字符串，包含竞品信息摘要
    """
    return _unavailable_response(f"{company_or_product} {industry}", "search_competitor_info")


@tool
def search_industry_benchmarks(industry: str, metric: str = "") -> str:
    """
    搜索行业基准数据（毛利率、获客成本、增长率等）。

    参数:
        industry: 行业名称，如 "SaaS" "餐饮" "电商"
        metric: 具体指标，如 "毛利率" "获客成本" "增长率"，留空则搜索综合基准

    返回: JSON 字符串，包含行业基准数据
    """
    return _unavailable_response(f"{industry} {metric}", "search_industry_benchmarks")
