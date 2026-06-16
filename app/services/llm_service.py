"""LLM 服务：接入阿里云百炼应用 API。

本模块只负责两类报告生成：
1. 新闻总结：写入 Prediction.news_llm_report，并通过接口返回 news_llm_report；
2. 整体总结：写入 Prediction.report_text，并通过接口返回 llm_report。

设计原则：
- API Key 只在后端 .env 中配置，前端不直接调用百炼；
- LLM 是增强能力，调用失败不能阻断预测主流程；
- 未配置百炼时返回 None，由 prediction_service 使用本地模板降级。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)


def _is_enabled(app_id: str | None) -> bool:
    """判断某个百炼应用是否可调用。"""
    return bool(settings.bailian_enable and settings.dashscope_api_key and app_id)


def _extract_text(payload: dict[str, Any]) -> str | None:
    """从百炼应用 API 响应中提取文本。

DashScope 应用 API 标准响应一般是 output.text。这里额外兼容少量
choices/message 形态，避免后续应用类型变化时前端拿不到报告。
"""
    output = payload.get("output")
    if isinstance(output, dict):
        text = output.get("text")
        if text:
            return str(text).strip()

        choices = output.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict) and message.get("content"):
                    return str(message["content"]).strip()
                if first.get("text"):
                    return str(first["text"]).strip()

    text = payload.get("text")
    if text:
        return str(text).strip()

    return None


def call_bailian_app(app_id: str | None, prompt: str) -> str | None:
    """调用阿里云百炼应用 API。

返回：
- 成功：生成文本；
- 未配置、超时、HTTP 非 200、响应格式不符合预期：None。

注意：这里不会抛异常到上层，避免 LLM 服务故障导致预测接口失败。
"""
    if not _is_enabled(app_id):
        return None

    url = f"{settings.bailian_base_url.rstrip('/')}/apps/{app_id}/completion"
    headers = {
        "Authorization": f"Bearer {settings.dashscope_api_key}",
        "Content-Type": "application/json",
    }
    if settings.bailian_workspace_id:
        headers["X-DashScope-WorkSpace"] = settings.bailian_workspace_id

    body = {
        "input": {"prompt": prompt},
        "parameters": {},
        "debug": {},
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            json=body,
            timeout=settings.bailian_timeout_seconds,
        )
    except requests.RequestException as exc:
        logger.warning("Bailian request failed: %s", exc)
        return None

    if response.status_code != 200:
        logger.warning(
            "Bailian request returned non-200 status: status=%s body=%s",
            response.status_code,
            response.text[:500],
        )
        return None

    try:
        payload = response.json()
    except ValueError:
        logger.warning("Bailian response is not valid JSON: %s", response.text[:500])
        return None

    text = _extract_text(payload) if isinstance(payload, dict) else None
    if not text:
        logger.warning("Bailian response does not contain output text: %s", str(payload)[:500])
        return None

    return text


def build_news_summary_prompt(
    *,
    ticker: str,
    company_name: str | None,
    base_trading_date: str | None,
    news_summary: dict[str, Any] | None,
    latest_news: list[dict[str, Any]],
) -> str:
    """构造新闻总结提示词。"""
    compact_news = []
    for item in latest_news[:10]:
        compact_news.append(
            {
                "title": item.get("title"),
                "summary": item.get("summary"),
                "source": item.get("source"),
                "publish_time": item.get("publish_time"),
                "sentiment_label": item.get("sentiment_label"),
                "sentiment_score": item.get("sentiment_score"),
            }
        )

    return f"""
你是 Finsight 金融新闻分析助手。请根据输入的股票新闻和情绪统计，生成“新闻总结”。

要求：
1. 使用简体中文。
2. 只能基于输入内容分析，不得编造新闻、公司事件或市场数据。
3. 不要给出“必涨”“必跌”等确定性判断。
4. 输出三段，分别为：新闻概况、正面与负面因素、短期情绪影响。
5. 控制在 250 字以内。
6. 最后一句提示“仅供课程实践和模拟分析参考，不构成投资建议”。

股票代码：{ticker}
公司名称：{company_name or "未知"}
预测基准日：{base_trading_date or "未知"}

新闻情绪统计：
{json.dumps(news_summary or {}, ensure_ascii=False, indent=2)}

相关新闻列表：
{json.dumps(compact_news, ensure_ascii=False, indent=2)}
""".strip()


def build_overall_report_prompt(
    *,
    ticker: str,
    company_name: str | None,
    base_trading_date: str | None,
    forecast_days: int,
    current_price: float | None,
    classification: dict[str, Any],
    regression: dict[str, Any],
    recommendation: dict[str, Any],
    news_summary: dict[str, Any] | None,
    news_llm_report: str | None,
    explanations: list[str] | None = None,
) -> str:
    """构造整体总结提示词。"""
    price_path = regression.get("price_path") or []
    compact_path = price_path[:forecast_days]

    return f"""
你是 Finsight 股票趋势预测系统的综合报告生成助手。请根据模型输出、预测价格路径、推荐分数和新闻总结，生成“整体总结”。

要求：
1. 使用简体中文。
2. 必须基于输入数据，不得虚构行情、新闻、财务数据或外部事件。
3. 必须说明这是模型分析结果，不能作为真实投资建议。
4. 输出四段，分别为：综合判断、模型依据、新闻因素、风险提示。
5. 控制在 350 字以内。

股票代码：{ticker}
公司名称：{company_name or "未知"}
预测基准日：{base_trading_date or "未知"}
预测天数：{forecast_days}
当前价格：{current_price}

分类模型结果：
{json.dumps(classification, ensure_ascii=False, indent=2)}

回归价格路径：
{json.dumps(compact_path, ensure_ascii=False, indent=2)}

推荐信息：
{json.dumps(recommendation, ensure_ascii=False, indent=2)}

新闻情绪摘要：
{json.dumps(news_summary or {}, ensure_ascii=False, indent=2)}

新闻总结：
{news_llm_report or "暂无"}

规则解释：
{json.dumps(explanations or [], ensure_ascii=False, indent=2)}
""".strip()


def generate_news_llm_report(
    *,
    ticker: str,
    company_name: str | None,
    base_trading_date: str | None,
    news_summary: dict[str, Any] | None,
    latest_news: list[dict[str, Any]],
) -> str | None:
    prompt = build_news_summary_prompt(
        ticker=ticker,
        company_name=company_name,
        base_trading_date=base_trading_date,
        news_summary=news_summary,
        latest_news=latest_news,
    )
    return call_bailian_app(settings.bailian_news_app_id, prompt)


def generate_overall_llm_report(
    *,
    ticker: str,
    company_name: str | None,
    base_trading_date: str | None,
    forecast_days: int,
    current_price: float | None,
    classification: dict[str, Any],
    regression: dict[str, Any],
    recommendation: dict[str, Any],
    news_summary: dict[str, Any] | None,
    news_llm_report: str | None,
    explanations: list[str] | None = None,
) -> str | None:
    prompt = build_overall_report_prompt(
        ticker=ticker,
        company_name=company_name,
        base_trading_date=base_trading_date,
        forecast_days=forecast_days,
        current_price=current_price,
        classification=classification,
        regression=regression,
        recommendation=recommendation,
        news_summary=news_summary,
        news_llm_report=news_llm_report,
        explanations=explanations,
    )
    return call_bailian_app(settings.bailian_report_app_id, prompt)
