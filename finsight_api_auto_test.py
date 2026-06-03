#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Finsight API 自动化测试脚本 v1.3

作用：
1. 自动登录普通用户和管理员，自动携带 JWT Token。
2. 按当前 v1.3 后端能力测试核心 API：
   - health
   - auth
   - stocks
   - watchlist
   - models
   - data-pipeline coverage / job
   - predictions
   - prediction history/detail
   - crawler status / daily-refresh
   - logs
   - admin users
   - backtest 接口壳
3. 记录每次请求的方法、URL、请求头、请求体、状态码、返回值和耗时。
4. 输出 JSON 与 Markdown 两份测试报告。
5. 对预测接口做结构校验：
   - classification 概率字段存在
   - 概率和接近 1
   - price_path 长度符合 forecast_days
   - model_version / reg_model_version 存在
   - data_refresh_status 存在
   - request_params 不再重复嵌套 data_refresh_status
   - news_summary.news_start_time / news_end_time 存在

默认后端地址：
    http://127.0.0.1:8002

基础运行：
    python finsight_api_auto_test.py --base-url http://127.0.0.1:8002

指定预测日期：
    python finsight_api_auto_test.py --prediction-base-date 2026-05-29

运行 Data Pipeline job：
    python finsight_api_auto_test.py --run-data-pipeline --pipeline-ticker GOOGL --pipeline-target-date 2026-05-29

运行每日补全兼容接口：
    python finsight_api_auto_test.py --run-daily-refresh --daily-refresh-target-date 2026-05-29

依赖：
    pip install requests
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import requests


SENSITIVE_KEYS = {
    "password",
    "confirm_password",
    "new_password",
    "token",
    "authorization",
    "Authorization",
    "ALPHA_VANTAGE_API_KEY",
}


def now_str() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def mask_sensitive(obj: Any) -> Any:
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            if str(k) in SENSITIVE_KEYS or str(k).lower() in {x.lower() for x in SENSITIVE_KEYS}:
                result[k] = "***MASKED***"
            else:
                result[k] = mask_sensitive(v)
        return result
    if isinstance(obj, list):
        return [mask_sensitive(x) for x in obj]
    return obj


def safe_json(obj: Any) -> Any:
    try:
        json.dumps(obj, ensure_ascii=False)
        return obj
    except TypeError:
        return str(obj)


def pretty(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)


@dataclass
class TestRecord:
    name: str
    method: str
    url: str
    expected_status: list[int]
    request_headers: dict[str, Any] = field(default_factory=dict)
    request_params: Optional[dict[str, Any]] = None
    request_json: Optional[dict[str, Any]] = None
    status_code: Optional[int] = None
    elapsed_ms: Optional[float] = None
    response: Any = None
    ok: bool = False
    error: Optional[str] = None
    validation_errors: list[str] = field(default_factory=list)


class FinsightApiTester:
    def __init__(
        self,
        *,
        base_url: str,
        admin_user: str,
        admin_pass: str,
        normal_user: str,
        normal_pass: str,
        output_dir: str,
        timeout: int,
        prediction_ticker: str,
        prediction_base_date: str | None,
        forecast_days: int,
        pipeline_ticker: str,
        pipeline_target_date: str | None,
        pipeline_modules: list[str],
        run_data_pipeline: bool,
        run_daily_refresh: bool,
        daily_refresh_target_date: str | None,
        run_on_demand_prediction: bool,
        on_demand_ticker: str,
        strict_prediction_checks: bool,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.admin_user = admin_user
        self.admin_pass = admin_pass
        self.normal_user = normal_user
        self.normal_pass = normal_pass
        self.timeout = timeout
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.prediction_ticker = prediction_ticker.upper()
        self.prediction_base_date = prediction_base_date
        self.forecast_days = forecast_days

        self.pipeline_ticker = pipeline_ticker.upper()
        self.pipeline_target_date = pipeline_target_date
        self.pipeline_modules = pipeline_modules
        self.run_data_pipeline = run_data_pipeline
        self.run_daily_refresh = run_daily_refresh
        self.daily_refresh_target_date = daily_refresh_target_date

        self.run_on_demand_prediction = run_on_demand_prediction
        self.on_demand_ticker = on_demand_ticker.upper()
        self.strict_prediction_checks = strict_prediction_checks

        self.records: list[TestRecord] = []
        self.admin_token: Optional[str] = None
        self.user_token: Optional[str] = None
        self.created_prediction_id: Optional[int] = None
        self.created_backtest_run_id: Optional[int] = None

    def request(
        self,
        name: str,
        method: str,
        path: str,
        *,
        token: Optional[str] = None,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
        expected_status: Optional[list[int]] = None,
        validator: Optional[Callable[[Any], list[str]]] = None,
    ) -> tuple[Optional[requests.Response], TestRecord]:
        expected_status = expected_status or [200]
        url = f"{self.base_url}{path}"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        record = TestRecord(
            name=name,
            method=method.upper(),
            url=url,
            expected_status=expected_status,
            request_headers=mask_sensitive(headers),
            request_params=mask_sensitive(params),
            request_json=mask_sensitive(json_body),
        )

        response: Optional[requests.Response] = None
        start = time.time()
        try:
            response = requests.request(
                method=method.upper(),
                url=url,
                headers=headers,
                params=params,
                json=json_body,
                timeout=self.timeout,
            )
            record.elapsed_ms = round((time.time() - start) * 1000, 2)
            record.status_code = response.status_code

            try:
                record.response = response.json()
            except Exception:
                record.response = response.text[:5000]

            record.ok = response.status_code in expected_status

            if record.ok and validator is not None:
                record.validation_errors = validator(record.response)
                if record.validation_errors:
                    record.ok = False

        except Exception as exc:
            record.elapsed_ms = round((time.time() - start) * 1000, 2)
            record.error = repr(exc)
            record.ok = False

        self.records.append(record)
        status = record.status_code if record.status_code is not None else "ERR"
        suffix = ""
        if record.validation_errors:
            suffix = f" validation_errors={len(record.validation_errors)}"
        print(f"[{'PASS' if record.ok else 'FAIL'}] {record.name} -> {status} ({record.elapsed_ms} ms){suffix}")
        return response, record

    @staticmethod
    def get_data(resp: Optional[requests.Response]) -> dict[str, Any]:
        if resp is None:
            return {}
        try:
            body = resp.json()
            data = body.get("data")
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def get_items(resp: Optional[requests.Response]) -> list[dict[str, Any]]:
        data = FinsightApiTester.get_data(resp)
        items = data.get("items")
        return items if isinstance(items, list) else []

    def run_all(self) -> None:
        self.test_health()
        self.test_auth()
        self.test_stock_api()
        self.test_watchlist_api()
        self.test_model_api()
        self.test_data_pipeline_api()
        self.test_prediction_api()
        self.test_backtest_api()
        self.test_crawler_api()
        self.test_log_api()
        self.test_admin_user_api()
        self.write_reports()

    def test_health(self) -> None:
        self.request("Health Check", "GET", "/health", expected_status=[200])

    def test_auth(self) -> None:
        resp, _ = self.request(
            "Auth - Login Normal User",
            "POST",
            "/api/auth/login",
            json_body={"username": self.normal_user, "password": self.normal_pass},
            expected_status=[200],
        )
        self.user_token = self.get_data(resp).get("token")

        resp, _ = self.request(
            "Auth - Login Admin",
            "POST",
            "/api/auth/login",
            json_body={"username": self.admin_user, "password": self.admin_pass},
            expected_status=[200],
        )
        self.admin_token = self.get_data(resp).get("token")

        self.request("Auth - Me Normal", "GET", "/api/auth/me", token=self.user_token, expected_status=[200, 401])
        self.request("Auth - Me Admin", "GET", "/api/auth/me", token=self.admin_token, expected_status=[200, 401])

    def test_stock_api(self) -> None:
        ticker = self.prediction_ticker

        resp, _ = self.request(
            f"Stock - Search {ticker}",
            "GET",
            "/api/stocks/search",
            token=self.user_token,
            params={"keyword": ticker, "only_supported": False, "include_etf": True, "limit": 10},
            expected_status=[200],
        )
        items = self.get_items(resp)
        if items:
            ticker = items[0].get("ticker") or ticker

        self.request(
            "Stock - Detail",
            "GET",
            f"/api/stocks/{ticker}/detail",
            token=self.user_token,
            params={"range": "1m", "include_news": True, "include_indicators": True, "auto_refresh": False},
            expected_status=[200, 404],
        )

        news_resp, _ = self.request(
            "Stock - News List",
            "GET",
            f"/api/stocks/{ticker}/news",
            token=self.user_token,
            params={"limit": 5},
            expected_status=[200, 404],
        )
        news_items = self.get_items(news_resp)
        if news_items:
            news_id = news_items[0].get("news_id") or news_items[0].get("id")
            if news_id is not None:
                self.request(
                    "Stock - News Detail",
                    "GET",
                    f"/api/stocks/news/{news_id}",
                    token=self.user_token,
                    params={"include_html": False},
                    expected_status=[200, 404],
                )

        self.request(
            "Stock - Sentiment Summary",
            "GET",
            f"/api/stocks/{ticker}/sentiment-summary",
            token=self.user_token,
            params={"window_days": 14},
            expected_status=[200, 404],
        )

    def test_watchlist_api(self) -> None:
        ticker = self.prediction_ticker
        self.request(
            f"Watchlist - Add {ticker}",
            "POST",
            "/api/watchlist",
            token=self.user_token,
            json_body={"ticker": ticker, "auto_fetch": False},
            expected_status=[200, 400, 409],
        )
        self.request(
            "Watchlist - List",
            "GET",
            "/api/watchlist",
            token=self.user_token,
            params={"include_curve": True},
            expected_status=[200],
        )
        self.request(
            f"Watchlist - Delete {ticker}",
            "DELETE",
            f"/api/watchlist/{ticker}",
            token=self.user_token,
            expected_status=[200, 404],
        )

    def test_model_api(self) -> None:
        self.request(
            "Model - Active Models",
            "GET",
            "/api/models/active",
            token=self.user_token,
            expected_status=[200, 404],
        )

    def test_data_pipeline_api(self) -> None:
        self.request(
            "Data Pipeline - Coverage",
            "GET",
            "/api/data-pipeline/coverage",
            params={"ticker": self.pipeline_ticker, "end_date": self.pipeline_target_date},
            expected_status=[200],
            validator=self.validate_data_pipeline_coverage,
        )

        if self.run_data_pipeline:
            self.request(
                "Data Pipeline - Run Job",
                "POST",
                "/api/data-pipeline/jobs",
                json_body={
                    "tickers": [self.pipeline_ticker],
                    "end_date": self.pipeline_target_date,
                    "modules": self.pipeline_modules,
                    "force_refresh": False,
                    "run_async": False,
                },
                expected_status=[200],
                validator=self.validate_data_pipeline_job,
            )

    def test_prediction_api(self) -> None:
        body = {
            "ticker": self.prediction_ticker,
            "forecast_days": self.forecast_days,
            "analysis_mode": "full",
            "risk_profile": "balanced",
            "news_window_days": 14,
            "force_refresh": False,
        }
        if self.prediction_base_date:
            body["base_trading_date"] = self.prediction_base_date

        resp, _ = self.request(
            f"Prediction - Run {self.prediction_ticker}",
            "POST",
            "/api/predictions/run",
            token=self.user_token,
            json_body=body,
            expected_status=[200],
            validator=self.validate_prediction_response,
        )
        data = self.get_data(resp)
        self.created_prediction_id = data.get("prediction_id") or data.get("id")

        if self.run_on_demand_prediction:
            on_demand_body = {
                "ticker": self.on_demand_ticker,
                "forecast_days": self.forecast_days,
                "base_trading_date": self.prediction_base_date,
                "analysis_mode": "full",
                "risk_profile": "balanced",
                "news_window_days": 14,
                "force_refresh": False,
            }
            self.request(
                f"Prediction - On-demand {self.on_demand_ticker}",
                "POST",
                "/api/predictions/run",
                token=self.user_token,
                json_body=on_demand_body,
                expected_status=[200, 404, 500] if not self.strict_prediction_checks else [200],
                validator=self.validate_prediction_response if self.strict_prediction_checks else None,
            )

        hist_resp, _ = self.request(
            "Prediction - History",
            "GET",
            "/api/predictions/history",
            token=self.user_token,
            params={"ticker": self.prediction_ticker, "page": 1, "page_size": 20},
            expected_status=[200],
        )
        if not self.created_prediction_id:
            items = self.get_items(hist_resp)
            if items:
                self.created_prediction_id = items[0].get("prediction_id") or items[0].get("id")

        if self.created_prediction_id:
            self.request(
                "Prediction - Detail",
                "GET",
                f"/api/predictions/{self.created_prediction_id}",
                token=self.user_token,
                expected_status=[200, 404],
                validator=self.validate_prediction_response,
            )

    def test_backtest_api(self) -> None:
        resp, _ = self.request(
            "Backtest - Run",
            "POST",
            "/api/backtest/run",
            token=self.user_token,
            json_body={
                "run_name": "API Auto Test Backtest",
                "tickers": [self.prediction_ticker],
                "start_date": "2026-05-01",
                "end_date": "2026-05-29",
                "initial_cash": 10000,
                "forecast_days": 5,
                "max_position_ratio": 0.3,
                "max_holding_count": 3,
                "fee_rate": 0.0005,
                "benchmark": "SPY",
                "save_daily_positions": True,
                "save_event_logs": True,
                "animation_mode": "realtime",
            },
            expected_status=[200, 400, 404, 500],
        )
        data = self.get_data(resp)
        self.created_backtest_run_id = data.get("run_id") or data.get("id") or 1

        run_id = self.created_backtest_run_id
        self.request("Backtest - Status", "GET", f"/api/backtest/{run_id}/status", token=self.user_token, expected_status=[200, 404])
        self.request("Backtest - Frames", "GET", f"/api/backtest/{run_id}/frames", token=self.user_token, params={"limit": 3}, expected_status=[200, 404])
        self.request("Backtest - Logs", "GET", f"/api/backtest/{run_id}/logs", token=self.user_token, params={"limit": 20}, expected_status=[200, 404])
        self.request("Backtest - Summary", "GET", f"/api/backtest/{run_id}/summary", token=self.user_token, expected_status=[200, 404])
        self.request("Backtest - Latest Final Positions", "GET", "/api/backtest/latest/final-positions", token=self.user_token, params={"include_empty": True}, expected_status=[200, 404])

    def test_crawler_api(self) -> None:
        self.request("Crawler - Status", "GET", "/api/crawler/status", token=self.admin_token, expected_status=[200, 403, 404])
        self.request("Crawler - Stock Universe Status", "GET", "/api/crawler/stock-universe/status", token=self.admin_token, expected_status=[200, 403, 404])

        if self.run_daily_refresh:
            self.request(
                "Crawler - Daily Refresh Run",
                "POST",
                "/api/crawler/daily-refresh/run",
                token=self.admin_token,
                json_body={
                    "tickers": [self.prediction_ticker],
                    "target_date": self.daily_refresh_target_date,
                    "force_refresh": False,
                    "limit": 1,
                },
                expected_status=[200, 400, 403, 404, 500],
            )

        self.request("Crawler - Daily Refresh Status", "GET", "/api/crawler/daily-refresh/status", token=self.admin_token, expected_status=[200, 403, 404])

    def test_log_api(self) -> None:
        self.request("Log - Query Logs", "GET", "/api/logs", token=self.admin_token, params={"page": 1, "page_size": 20}, expected_status=[200, 403])

    def test_admin_user_api(self) -> None:
        self.request("Admin - User List", "GET", "/api/admin/users", token=self.admin_token, params={"page": 1, "page_size": 20}, expected_status=[200, 403])

    def validate_data_pipeline_coverage(self, body: Any) -> list[str]:
        errors: list[str] = []
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            return ["data is missing or not an object"]

        for key in ["price_data", "technical_indicators", "news_data", "sentiment_daily", "model_feature_snapshots", "recommendation"]:
            if key not in data:
                errors.append(f"coverage missing {key}")
        return errors

    def validate_data_pipeline_job(self, body: Any) -> list[str]:
        errors: list[str] = []
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            return ["data is missing or not an object"]

        for key in ["job_id", "status", "tickers", "modules", "items"]:
            if key not in data:
                errors.append(f"pipeline job missing {key}")
        return errors

    def validate_prediction_response(self, body: Any) -> list[str]:
        errors: list[str] = []
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            return ["prediction data is missing"]

        required_top = [
            "prediction_id",
            "ticker",
            "base_trading_date",
            "forecast_days",
            "model_version",
            "reg_model_version",
            "request_params",
            "classification",
            "regression",
            "data_refresh_status",
            "news_summary",
        ]
        for key in required_top:
            if key not in data:
                errors.append(f"missing top field: {key}")

        request_params = data.get("request_params")
        if isinstance(request_params, dict) and "data_refresh_status" in request_params:
            errors.append("request_params should not contain data_refresh_status")

        cls = data.get("classification") or {}
        for key in ["predicted_label", "prob_up", "prob_neutral", "prob_down", "predicted_growth_prob"]:
            if key not in cls:
                errors.append(f"classification missing {key}")

        try:
            prob_sum = float(cls.get("prob_up", 0)) + float(cls.get("prob_neutral", 0)) + float(cls.get("prob_down", 0))
            if not math.isclose(prob_sum, 1.0, rel_tol=1e-3, abs_tol=1e-3):
                errors.append(f"classification probabilities sum to {prob_sum}, not close to 1")
        except Exception:
            errors.append("classification probabilities are not numeric")

        reg = data.get("regression") or {}
        path = reg.get("price_path")
        if not isinstance(path, list):
            errors.append("regression.price_path should be a list")
        else:
            expected_len = int(data.get("forecast_days") or self.forecast_days)
            if len(path) != expected_len:
                errors.append(f"price_path length {len(path)} != forecast_days {expected_len}")

        refresh = data.get("data_refresh_status") or {}
        if not isinstance(refresh, dict):
            errors.append("data_refresh_status should be an object")
        else:
            for key in ["status", "ticker", "can_continue"]:
                if key not in refresh:
                    errors.append(f"data_refresh_status missing {key}")

        news = data.get("news_summary") or {}
        if isinstance(news, dict):
            if not news.get("news_start_time"):
                errors.append("news_summary.news_start_time is missing")
            if not news.get("news_end_time"):
                errors.append("news_summary.news_end_time is missing")
        else:
            errors.append("news_summary should be an object")

        return errors

    def write_reports(self) -> None:
        ts = now_str()
        json_path = self.output_dir / f"finsight_api_test_report_{ts}.json"
        md_path = self.output_dir / f"finsight_api_test_report_{ts}.md"

        records = [
            {
                "name": r.name,
                "method": r.method,
                "url": r.url,
                "expected_status": r.expected_status,
                "request_headers": r.request_headers,
                "request_params": r.request_params,
                "request_json": r.request_json,
                "status_code": r.status_code,
                "elapsed_ms": r.elapsed_ms,
                "ok": r.ok,
                "error": r.error,
                "validation_errors": r.validation_errors,
                "response": safe_json(r.response),
            }
            for r in self.records
        ]

        summary = {
            "base_url": self.base_url,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total": len(self.records),
            "passed": sum(1 for r in self.records if r.ok),
            "failed": sum(1 for r in self.records if not r.ok),
        }

        json_path.write_text(json.dumps({"summary": summary, "records": records}, ensure_ascii=False, indent=2), encoding="utf-8")

        lines: list[str] = []
        lines.append("# Finsight API 自动化测试报告")
        lines.append("")
        lines.append(f"- Base URL: `{self.base_url}`")
        lines.append(f"- Generated At: `{summary['generated_at']}`")
        lines.append(f"- Total: **{summary['total']}**")
        lines.append(f"- Passed: **{summary['passed']}**")
        lines.append(f"- Failed: **{summary['failed']}**")
        lines.append("")
        lines.append("## 汇总表")
        lines.append("")
        lines.append("| # | 结果 | 接口 | 方法 | 状态码 | 耗时 ms | 校验错误 |")
        lines.append("|---:|---|---|---|---:|---:|---:|")
        for idx, r in enumerate(self.records, 1):
            result = "✅ PASS" if r.ok else "❌ FAIL"
            lines.append(f"| {idx} | {result} | {r.name} | {r.method} | {r.status_code} | {r.elapsed_ms} | {len(r.validation_errors)} |")

        lines.append("")
        lines.append("## 详细请求与返回")
        for idx, r in enumerate(self.records, 1):
            result = "PASS" if r.ok else "FAIL"
            lines.append("")
            lines.append(f"### {idx}. {r.name} - {result}")
            lines.append("")
            lines.append(f"- Method: `{r.method}`")
            lines.append(f"- URL: `{r.url}`")
            lines.append(f"- Expected Status: `{r.expected_status}`")
            lines.append(f"- Actual Status: `{r.status_code}`")
            lines.append(f"- Elapsed: `{r.elapsed_ms} ms`")
            if r.error:
                lines.append(f"- Error: `{r.error}`")
            if r.validation_errors:
                lines.append("- Validation Errors:")
                for err in r.validation_errors:
                    lines.append(f"  - `{err}`")

            lines.append("")
            lines.append("**Request Headers**")
            lines.append("```json")
            lines.append(pretty(r.request_headers))
            lines.append("```")

            if r.request_params is not None:
                lines.append("**Request Params**")
                lines.append("```json")
                lines.append(pretty(r.request_params))
                lines.append("```")

            if r.request_json is not None:
                lines.append("**Request JSON**")
                lines.append("```json")
                lines.append(pretty(r.request_json))
                lines.append("```")

            lines.append("**Response**")
            lines.append("```json")
            lines.append(pretty(r.response))
            lines.append("```")

        md_path.write_text("\n".join(lines), encoding="utf-8")

        print("\n测试完成：")
        print(f"  JSON 报告：{json_path}")
        print(f"  Markdown 报告：{md_path}")
        print(f"  Total={summary['total']} Passed={summary['passed']} Failed={summary['failed']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Finsight API 自动化测试脚本 v1.3")
    parser.add_argument("--base-url", default="http://127.0.0.1:8002", help="后端服务地址")
    parser.add_argument("--admin-user", default="admin", help="管理员用户名")
    parser.add_argument("--admin-pass", default="Admin123", help="管理员密码")
    parser.add_argument("--user", default="user01", help="普通用户用户名")
    parser.add_argument("--user-pass", default="User123", help="普通用户密码")
    parser.add_argument("--output-dir", default="api_test_results", help="测试报告输出目录")
    parser.add_argument("--timeout", type=int, default=30, help="单个请求超时时间，秒")

    parser.add_argument("--prediction-ticker", default="GOOGL", help="预测测试 ticker")
    parser.add_argument("--prediction-base-date", default="2026-05-29", help="预测基准日")
    parser.add_argument("--forecast-days", type=int, default=5, help="预测天数，当前建议 1~5")

    parser.add_argument("--pipeline-ticker", default="GOOGL", help="Data Pipeline 测试 ticker")
    parser.add_argument("--pipeline-target-date", default="2026-05-29", help="Data Pipeline 目标日期")
    parser.add_argument("--pipeline-modules", default="market,technical,news,sentiment,features", help="Data Pipeline 模块列表")
    parser.add_argument("--run-data-pipeline", action="store_true", help="是否执行 Data Pipeline job，默认只查 coverage")

    parser.add_argument("--run-daily-refresh", action="store_true", help="是否测试每日补全兼容接口")
    parser.add_argument("--daily-refresh-target-date", default="2026-05-29", help="每日补全目标日期")

    parser.add_argument("--run-on-demand-prediction", action="store_true", help="是否额外测试一个 on-demand 预测 ticker")
    parser.add_argument("--on-demand-ticker", default="META", help="on-demand 预测测试 ticker")

    parser.add_argument("--strict-prediction-checks", action="store_true", help="on-demand 预测也严格要求 200 和结构校验")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    tester = FinsightApiTester(
        base_url=args.base_url,
        admin_user=args.admin_user,
        admin_pass=args.admin_pass,
        normal_user=args.user,
        normal_pass=args.user_pass,
        output_dir=args.output_dir,
        timeout=args.timeout,
        prediction_ticker=args.prediction_ticker,
        prediction_base_date=args.prediction_base_date,
        forecast_days=args.forecast_days,
        pipeline_ticker=args.pipeline_ticker,
        pipeline_target_date=args.pipeline_target_date,
        pipeline_modules=[x.strip() for x in args.pipeline_modules.split(",") if x.strip()],
        run_data_pipeline=args.run_data_pipeline,
        run_daily_refresh=args.run_daily_refresh,
        daily_refresh_target_date=args.daily_refresh_target_date,
        run_on_demand_prediction=args.run_on_demand_prediction,
        on_demand_ticker=args.on_demand_ticker,
        strict_prediction_checks=args.strict_prediction_checks,
    )
    tester.run_all()


if __name__ == "__main__":
    main()
