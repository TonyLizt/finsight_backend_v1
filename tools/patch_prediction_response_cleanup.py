#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Patch prediction_service.py response cleanup.

修复两个 API 返回结构问题：
1. request_params 中重复嵌入 data_refresh_status；
2. news_summary.news_start_time / news_end_time 为空。

用法：
    python tools/patch_prediction_response_cleanup.py

说明：
- 幂等执行，重复运行不会重复插入 helper；
- 会备份原文件为 app/services/prediction_service.py.bak_response_cleanup；
- 如果你的 prediction_service.py 文本结构和脚本假设不一致，会抛出明确错误。
"""

from __future__ import annotations

from pathlib import Path


TARGET = Path("app/services/prediction_service.py")
BACKUP = TARGET.with_suffix(".py.bak_response_cleanup")


HELPER = """
def _clean_request_params(params: dict | None) -> dict:
    \"\"\"清理保存和返回给前端的请求参数。

    request_params 只应该保存用户原始请求参数，不应重复嵌入运行时状态。
    运行时状态统一放在顶层 data_refresh_status，以及 explanation_json.data_refresh_status。
    \"\"\"
    if not isinstance(params, dict):
        return {}

    cleaned = dict(params)
    cleaned.pop("data_refresh_status", None)
    return cleaned


def _normalize_news_summary(summary: dict | None) -> dict | None:
    \"\"\"补全新闻情绪摘要的起止时间。

    当前 sentiment_daily 聚合通常能给出 sentiment_curve，但部分路径下
    news_start_time / news_end_time 为空。前端展示时需要明确窗口范围，因此：
    - 如果 news_start_time 为空，则使用 sentiment_curve 第一项的 date；
    - 如果 news_end_time 为空，则使用 sentiment_curve 最后一项的 date。
    \"\"\"
    if not isinstance(summary, dict):
        return summary

    normalized = dict(summary)
    curve = normalized.get("sentiment_curve")

    if isinstance(curve, list) and curve:
        dates = [
            str(item.get("date"))
            for item in curve
            if isinstance(item, dict) and item.get("date")
        ]

        if dates:
            if not normalized.get("news_start_time"):
                normalized["news_start_time"] = dates[0]
            if not normalized.get("news_end_time"):
                normalized["news_end_time"] = dates[-1]

    return normalized


"""


def insert_helper(text: str) -> str:
    """在 run_prediction 前插入 helper 函数。"""
    if "def _clean_request_params(" in text and "def _normalize_news_summary(" in text:
        return text

    marker = "\ndef run_prediction("
    if marker not in text:
        raise RuntimeError("Could not find def run_prediction(...) marker in prediction_service.py")

    return text.replace(marker, "\n" + HELPER + "def run_prediction(", 1)


def patch_before_prediction_insert(text: str) -> str:
    """在 pred = Prediction(...) 前加入 clean_request_params 和 news_summary 标准化。"""
    if "clean_request_params = _clean_request_params(req.model_dump(mode=\"json\"))" in text:
        return text

    marker = "    pred = Prediction(\n"
    insert = (
        "    # 返回给前端和保存到数据库的 request_params 只保留用户原始请求，\n"
        "    # 不重复嵌入 data_refresh_status。\n"
        "    clean_request_params = _clean_request_params(req.model_dump(mode=\"json\"))\n"
        "\n"
        "    # 保证 news_summary 中有可展示的窗口起止时间。\n"
        "    sentiment = _normalize_news_summary(sentiment)\n"
        "\n"
    )

    if marker not in text:
        raise RuntimeError("Could not find 'pred = Prediction(' marker in prediction_service.py")

    return text.replace(marker, insert + marker, 1)


def patch_request_params_save(text: str) -> str:
    """替换保存 Prediction 时的 request_params_json。"""
    patterns = [
        'request_params_json={**req.model_dump(mode="json"), "data_refresh_status": data_refresh_status},',
        "request_params_json={**req.model_dump(mode='json'), 'data_refresh_status': data_refresh_status},",
    ]

    for old in patterns:
        if old in text:
            text = text.replace(old, "request_params_json=clean_request_params,")

    return text


def patch_prediction_to_detail(text: str) -> str:
    """修复 prediction_to_detail 的返回字段。"""
    text = text.replace(
        '"request_params": pred.request_params_json,',
        '"request_params": _clean_request_params(pred.request_params_json),',
    )

    text = text.replace(
        '"news_summary": pred.sentiment_summary_json,',
        '"news_summary": _normalize_news_summary(pred.sentiment_summary_json),',
    )

    return text


def main() -> None:
    if not TARGET.exists():
        raise FileNotFoundError(f"{TARGET} not found. Please run at project root.")

    original = TARGET.read_text(encoding="utf-8")
    text = original

    text = insert_helper(text)
    text = patch_before_prediction_insert(text)
    text = patch_request_params_save(text)
    text = patch_prediction_to_detail(text)

    if text == original:
        print("No changes needed.")
        return

    if not BACKUP.exists():
        BACKUP.write_text(original, encoding="utf-8")

    TARGET.write_text(text, encoding="utf-8")
    print(f"Updated: {TARGET}")
    print(f"Backup:  {BACKUP}")


if __name__ == "__main__":
    main()
