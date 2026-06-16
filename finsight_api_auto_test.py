# #!/usr/bin/env python3
# # -*- coding: utf-8 -*-
# """
# Finsight API 自动化测试脚本 v1.3

# 作用：
# 1. 自动登录普通用户和管理员，自动携带 JWT Token。
# 2. 按当前 v1.3 后端能力测试核心 API：
#    - health
#    - auth
#    - stocks
#    - watchlist
#    - models
#    - data-pipeline coverage / job
#    - predictions
#    - prediction history/detail
#    - crawler status / daily-refresh
#    - logs
#    - admin users
#    - backtest 接口壳
# 3. 记录每次请求的方法、URL、请求头、请求体、状态码、返回值和耗时。
# 4. 输出 JSON 与 Markdown 两份测试报告。
# 5. 对预测接口做结构校验：
#    - classification 概率字段存在
#    - 概率和接近 1
#    - price_path 长度符合 forecast_days
#    - model_version / reg_model_version 存在
#    - data_refresh_status 存在
#    - request_params 不再重复嵌套 data_refresh_status
#    - news_summary.news_start_time / news_end_time 存在

# 默认后端地址：
#     http://127.0.0.1:8002

# 基础运行：
#     python finsight_api_auto_test.py --base-url http://127.0.0.1:8002

# 指定预测日期：
#     python finsight_api_auto_test.py --prediction-base-date 2026-05-29

# 运行 Data Pipeline job：
#     python finsight_api_auto_test.py --run-data-pipeline --pipeline-ticker GOOGL --pipeline-target-date 2026-05-29

# 运行每日补全兼容接口：
#     python finsight_api_auto_test.py --run-daily-refresh --daily-refresh-target-date 2026-05-29

# 依赖：
#     pip install requests
# """

# from __future__ import annotations

# import argparse
# import json
# import math
# import time
# from dataclasses import dataclass, field
# from datetime import datetime, timezone
# from pathlib import Path
# from typing import Any, Callable, Optional

# import requests


# SENSITIVE_KEYS = {
#     "password",
#     "confirm_password",
#     "new_password",
#     "token",
#     "authorization",
#     "Authorization",
#     "ALPHA_VANTAGE_API_KEY",
# }


# def now_str() -> str:
#     return datetime.now().strftime("%Y%m%d_%H%M%S")


# def mask_sensitive(obj: Any) -> Any:
#     if isinstance(obj, dict):
#         result = {}
#         for k, v in obj.items():
#             if str(k) in SENSITIVE_KEYS or str(k).lower() in {x.lower() for x in SENSITIVE_KEYS}:
#                 result[k] = "***MASKED***"
#             else:
#                 result[k] = mask_sensitive(v)
#         return result
#     if isinstance(obj, list):
#         return [mask_sensitive(x) for x in obj]
#     return obj


# def safe_json(obj: Any) -> Any:
#     try:
#         json.dumps(obj, ensure_ascii=False)
#         return obj
#     except TypeError:
#         return str(obj)


# def pretty(obj: Any) -> str:
#     try:
#         return json.dumps(obj, ensure_ascii=False, indent=2)
#     except Exception:
#         return str(obj)


# @dataclass
# class TestRecord:
#     name: str
#     method: str
#     url: str
#     expected_status: list[int]
#     request_headers: dict[str, Any] = field(default_factory=dict)
#     request_params: Optional[dict[str, Any]] = None
#     request_json: Optional[dict[str, Any]] = None
#     status_code: Optional[int] = None
#     elapsed_ms: Optional[float] = None
#     response: Any = None
#     ok: bool = False
#     error: Optional[str] = None
#     validation_errors: list[str] = field(default_factory=list)


# class FinsightApiTester:
#     def __init__(
#         self,
#         *,
#         base_url: str,
#         admin_user: str,
#         admin_pass: str,
#         normal_user: str,
#         normal_pass: str,
#         output_dir: str,
#         timeout: int,
#         prediction_ticker: str,
#         prediction_base_date: str | None,
#         forecast_days: int,
#         pipeline_ticker: str,
#         pipeline_target_date: str | None,
#         pipeline_modules: list[str],
#         run_data_pipeline: bool,
#         run_daily_refresh: bool,
#         daily_refresh_target_date: str | None,
#         run_on_demand_prediction: bool,
#         on_demand_ticker: str,
#         strict_prediction_checks: bool,
#     ) -> None:
#         self.base_url = base_url.rstrip("/")
#         self.admin_user = admin_user
#         self.admin_pass = admin_pass
#         self.normal_user = normal_user
#         self.normal_pass = normal_pass
#         self.timeout = timeout
#         self.output_dir = Path(output_dir)
#         self.output_dir.mkdir(parents=True, exist_ok=True)

#         self.prediction_ticker = prediction_ticker.upper()
#         self.prediction_base_date = prediction_base_date
#         self.forecast_days = forecast_days

#         self.pipeline_ticker = pipeline_ticker.upper()
#         self.pipeline_target_date = pipeline_target_date
#         self.pipeline_modules = pipeline_modules
#         self.run_data_pipeline = run_data_pipeline
#         self.run_daily_refresh = run_daily_refresh
#         self.daily_refresh_target_date = daily_refresh_target_date

#         self.run_on_demand_prediction = run_on_demand_prediction
#         self.on_demand_ticker = on_demand_ticker.upper()
#         self.strict_prediction_checks = strict_prediction_checks

#         self.records: list[TestRecord] = []
#         self.admin_token: Optional[str] = None
#         self.user_token: Optional[str] = None
#         self.created_prediction_id: Optional[int] = None
#         self.created_backtest_run_id: Optional[int] = None

#     def request(
#         self,
#         name: str,
#         method: str,
#         path: str,
#         *,
#         token: Optional[str] = None,
#         params: Optional[dict[str, Any]] = None,
#         json_body: Optional[dict[str, Any]] = None,
#         expected_status: Optional[list[int]] = None,
#         validator: Optional[Callable[[Any], list[str]]] = None,
#     ) -> tuple[Optional[requests.Response], TestRecord]:
#         expected_status = expected_status or [200]
#         url = f"{self.base_url}{path}"
#         headers: dict[str, str] = {"Content-Type": "application/json"}
#         if token:
#             headers["Authorization"] = f"Bearer {token}"

#         record = TestRecord(
#             name=name,
#             method=method.upper(),
#             url=url,
#             expected_status=expected_status,
#             request_headers=mask_sensitive(headers),
#             request_params=mask_sensitive(params),
#             request_json=mask_sensitive(json_body),
#         )

#         response: Optional[requests.Response] = None
#         start = time.time()
#         try:
#             response = requests.request(
#                 method=method.upper(),
#                 url=url,
#                 headers=headers,
#                 params=params,
#                 json=json_body,
#                 timeout=self.timeout,
#             )
#             record.elapsed_ms = round((time.time() - start) * 1000, 2)
#             record.status_code = response.status_code

#             try:
#                 record.response = response.json()
#             except Exception:
#                 record.response = response.text[:5000]

#             record.ok = response.status_code in expected_status

#             if record.ok and validator is not None:
#                 record.validation_errors = validator(record.response)
#                 if record.validation_errors:
#                     record.ok = False

#         except Exception as exc:
#             record.elapsed_ms = round((time.time() - start) * 1000, 2)
#             record.error = repr(exc)
#             record.ok = False

#         self.records.append(record)
#         status = record.status_code if record.status_code is not None else "ERR"
#         suffix = ""
#         if record.validation_errors:
#             suffix = f" validation_errors={len(record.validation_errors)}"
#         print(f"[{'PASS' if record.ok else 'FAIL'}] {record.name} -> {status} ({record.elapsed_ms} ms){suffix}")
#         return response, record

#     @staticmethod
#     def get_data(resp: Optional[requests.Response]) -> dict[str, Any]:
#         if resp is None:
#             return {}
#         try:
#             body = resp.json()
#             data = body.get("data")
#             return data if isinstance(data, dict) else {}
#         except Exception:
#             return {}

#     @staticmethod
#     def get_items(resp: Optional[requests.Response]) -> list[dict[str, Any]]:
#         data = FinsightApiTester.get_data(resp)
#         items = data.get("items")
#         return items if isinstance(items, list) else []

#     def run_all(self) -> None:
#         self.test_health()
#         self.test_auth()
#         self.test_stock_api()
#         self.test_watchlist_api()
#         self.test_model_api()
#         self.test_data_pipeline_api()
#         self.test_prediction_api()
#         self.test_backtest_api()
#         self.test_crawler_api()
#         self.test_log_api()
#         self.test_admin_user_api()
#         self.write_reports()

#     def test_health(self) -> None:
#         self.request("Health Check", "GET", "/health", expected_status=[200])

#     def test_auth(self) -> None:
#         resp, _ = self.request(
#             "Auth - Login Normal User",
#             "POST",
#             "/api/auth/login",
#             json_body={"username": self.normal_user, "password": self.normal_pass},
#             expected_status=[200],
#         )
#         self.user_token = self.get_data(resp).get("token")

#         resp, _ = self.request(
#             "Auth - Login Admin",
#             "POST",
#             "/api/auth/login",
#             json_body={"username": self.admin_user, "password": self.admin_pass},
#             expected_status=[200],
#         )
#         self.admin_token = self.get_data(resp).get("token")

#         self.request("Auth - Me Normal", "GET", "/api/auth/me", token=self.user_token, expected_status=[200, 401])
#         self.request("Auth - Me Admin", "GET", "/api/auth/me", token=self.admin_token, expected_status=[200, 401])

#     def test_stock_api(self) -> None:
#         ticker = self.prediction_ticker

#         resp, _ = self.request(
#             f"Stock - Search {ticker}",
#             "GET",
#             "/api/stocks/search",
#             token=self.user_token,
#             params={"keyword": ticker, "only_supported": False, "include_etf": True, "limit": 10},
#             expected_status=[200],
#         )
#         items = self.get_items(resp)
#         if items:
#             ticker = items[0].get("ticker") or ticker

#         self.request(
#             "Stock - Detail",
#             "GET",
#             f"/api/stocks/{ticker}/detail",
#             token=self.user_token,
#             params={"range": "1m", "include_news": True, "include_indicators": True, "auto_refresh": False},
#             expected_status=[200, 404],
#         )

#         news_resp, _ = self.request(
#             "Stock - News List",
#             "GET",
#             f"/api/stocks/{ticker}/news",
#             token=self.user_token,
#             params={"limit": 5},
#             expected_status=[200, 404],
#         )
#         news_items = self.get_items(news_resp)
#         if news_items:
#             news_id = news_items[0].get("news_id") or news_items[0].get("id")
#             if news_id is not None:
#                 self.request(
#                     "Stock - News Detail",
#                     "GET",
#                     f"/api/stocks/news/{news_id}",
#                     token=self.user_token,
#                     params={"include_html": False},
#                     expected_status=[200, 404],
#                 )

#         self.request(
#             "Stock - Sentiment Summary",
#             "GET",
#             f"/api/stocks/{ticker}/sentiment-summary",
#             token=self.user_token,
#             params={"window_days": 14},
#             expected_status=[200, 404],
#         )

#     def test_watchlist_api(self) -> None:
#         ticker = self.prediction_ticker
#         self.request(
#             f"Watchlist - Add {ticker}",
#             "POST",
#             "/api/watchlist",
#             token=self.user_token,
#             json_body={"ticker": ticker, "auto_fetch": False},
#             expected_status=[200, 400, 409],
#         )
#         self.request(
#             "Watchlist - List",
#             "GET",
#             "/api/watchlist",
#             token=self.user_token,
#             params={"include_curve": True},
#             expected_status=[200],
#         )
#         self.request(
#             f"Watchlist - Delete {ticker}",
#             "DELETE",
#             f"/api/watchlist/{ticker}",
#             token=self.user_token,
#             expected_status=[200, 404],
#         )

#     def test_model_api(self) -> None:
#         self.request(
#             "Model - Active Models",
#             "GET",
#             "/api/models/active",
#             token=self.user_token,
#             expected_status=[200, 404],
#         )

#     def test_data_pipeline_api(self) -> None:
#         self.request(
#             "Data Pipeline - Coverage",
#             "GET",
#             "/api/data-pipeline/coverage",
#             params={"ticker": self.pipeline_ticker, "end_date": self.pipeline_target_date},
#             expected_status=[200],
#             validator=self.validate_data_pipeline_coverage,
#         )

#         if self.run_data_pipeline:
#             self.request(
#                 "Data Pipeline - Run Job",
#                 "POST",
#                 "/api/data-pipeline/jobs",
#                 json_body={
#                     "tickers": [self.pipeline_ticker],
#                     "end_date": self.pipeline_target_date,
#                     "modules": self.pipeline_modules,
#                     "force_refresh": False,
#                     "run_async": False,
#                 },
#                 expected_status=[200],
#                 validator=self.validate_data_pipeline_job,
#             )

#     def test_prediction_api(self) -> None:
#         body = {
#             "ticker": self.prediction_ticker,
#             "forecast_days": self.forecast_days,
#             "analysis_mode": "full",
#             "risk_profile": "balanced",
#             "news_window_days": 14,
#             "force_refresh": False,
#         }
#         if self.prediction_base_date:
#             body["base_trading_date"] = self.prediction_base_date

#         resp, _ = self.request(
#             f"Prediction - Run {self.prediction_ticker}",
#             "POST",
#             "/api/predictions/run",
#             token=self.user_token,
#             json_body=body,
#             expected_status=[200],
#             validator=self.validate_prediction_response,
#         )
#         data = self.get_data(resp)
#         self.created_prediction_id = data.get("prediction_id") or data.get("id")

#         if self.run_on_demand_prediction:
#             on_demand_body = {
#                 "ticker": self.on_demand_ticker,
#                 "forecast_days": self.forecast_days,
#                 "base_trading_date": self.prediction_base_date,
#                 "analysis_mode": "full",
#                 "risk_profile": "balanced",
#                 "news_window_days": 14,
#                 "force_refresh": False,
#             }
#             self.request(
#                 f"Prediction - On-demand {self.on_demand_ticker}",
#                 "POST",
#                 "/api/predictions/run",
#                 token=self.user_token,
#                 json_body=on_demand_body,
#                 expected_status=[200, 404, 500] if not self.strict_prediction_checks else [200],
#                 validator=self.validate_prediction_response if self.strict_prediction_checks else None,
#             )

#         hist_resp, _ = self.request(
#             "Prediction - History",
#             "GET",
#             "/api/predictions/history",
#             token=self.user_token,
#             params={"ticker": self.prediction_ticker, "page": 1, "page_size": 20},
#             expected_status=[200],
#         )
#         if not self.created_prediction_id:
#             items = self.get_items(hist_resp)
#             if items:
#                 self.created_prediction_id = items[0].get("prediction_id") or items[0].get("id")

#         if self.created_prediction_id:
#             self.request(
#                 "Prediction - Detail",
#                 "GET",
#                 f"/api/predictions/{self.created_prediction_id}",
#                 token=self.user_token,
#                 expected_status=[200, 404],
#                 validator=self.validate_prediction_response,
#             )

#     def test_backtest_api(self) -> None:
#         resp, _ = self.request(
#             "Backtest - Run",
#             "POST",
#             "/api/backtest/run",
#             token=self.user_token,
#             json_body={
#                 "run_name": "API Auto Test Backtest",
#                 "tickers": [self.prediction_ticker],
#                 "start_date": "2026-05-01",
#                 "end_date": "2026-05-29",
#                 "initial_cash": 10000,
#                 "forecast_days": 5,
#                 "max_position_ratio": 0.3,
#                 "max_holding_count": 3,
#                 "fee_rate": 0.0005,
#                 "benchmark": "SPY",
#                 "save_daily_positions": True,
#                 "save_event_logs": True,
#                 "animation_mode": "realtime",
#             },
#             expected_status=[200, 400, 404, 500],
#         )
#         data = self.get_data(resp)
#         self.created_backtest_run_id = data.get("run_id") or data.get("id") or 1

#         run_id = self.created_backtest_run_id
#         self.request("Backtest - Status", "GET", f"/api/backtest/{run_id}/status", token=self.user_token, expected_status=[200, 404])
#         self.request("Backtest - Frames", "GET", f"/api/backtest/{run_id}/frames", token=self.user_token, params={"limit": 3}, expected_status=[200, 404])
#         self.request("Backtest - Logs", "GET", f"/api/backtest/{run_id}/logs", token=self.user_token, params={"limit": 20}, expected_status=[200, 404])
#         self.request("Backtest - Summary", "GET", f"/api/backtest/{run_id}/summary", token=self.user_token, expected_status=[200, 404])
#         self.request("Backtest - Latest Final Positions", "GET", "/api/backtest/latest/final-positions", token=self.user_token, params={"include_empty": True}, expected_status=[200, 404])

#     def test_crawler_api(self) -> None:
#         self.request("Crawler - Status", "GET", "/api/crawler/status", token=self.admin_token, expected_status=[200, 403, 404])
#         self.request("Crawler - Stock Universe Status", "GET", "/api/crawler/stock-universe/status", token=self.admin_token, expected_status=[200, 403, 404])

#         if self.run_daily_refresh:
#             self.request(
#                 "Crawler - Daily Refresh Run",
#                 "POST",
#                 "/api/crawler/daily-refresh/run",
#                 token=self.admin_token,
#                 json_body={
#                     "tickers": [self.prediction_ticker],
#                     "target_date": self.daily_refresh_target_date,
#                     "force_refresh": False,
#                     "limit": 1,
#                 },
#                 expected_status=[200, 400, 403, 404, 500],
#             )

#         self.request("Crawler - Daily Refresh Status", "GET", "/api/crawler/daily-refresh/status", token=self.admin_token, expected_status=[200, 403, 404])

#     def test_log_api(self) -> None:
#         self.request("Log - Query Logs", "GET", "/api/logs", token=self.admin_token, params={"page": 1, "page_size": 20}, expected_status=[200, 403])

#     def test_admin_user_api(self) -> None:
#         self.request("Admin - User List", "GET", "/api/admin/users", token=self.admin_token, params={"page": 1, "page_size": 20}, expected_status=[200, 403])

#     def validate_data_pipeline_coverage(self, body: Any) -> list[str]:
#         errors: list[str] = []
#         data = body.get("data") if isinstance(body, dict) else None
#         if not isinstance(data, dict):
#             return ["data is missing or not an object"]

#         for key in ["price_data", "technical_indicators", "news_data", "sentiment_daily", "model_feature_snapshots", "recommendation"]:
#             if key not in data:
#                 errors.append(f"coverage missing {key}")
#         return errors

#     def validate_data_pipeline_job(self, body: Any) -> list[str]:
#         errors: list[str] = []
#         data = body.get("data") if isinstance(body, dict) else None
#         if not isinstance(data, dict):
#             return ["data is missing or not an object"]

#         for key in ["job_id", "status", "tickers", "modules", "items"]:
#             if key not in data:
#                 errors.append(f"pipeline job missing {key}")
#         return errors

#     def validate_prediction_response(self, body: Any) -> list[str]:
#         errors: list[str] = []
#         data = body.get("data") if isinstance(body, dict) else None
#         if not isinstance(data, dict):
#             return ["prediction data is missing"]

#         required_top = [
#             "prediction_id",
#             "ticker",
#             "base_trading_date",
#             "forecast_days",
#             "model_version",
#             "reg_model_version",
#             "request_params",
#             "classification",
#             "regression",
#             "data_refresh_status",
#             "news_summary",
#         ]
#         for key in required_top:
#             if key not in data:
#                 errors.append(f"missing top field: {key}")

#         request_params = data.get("request_params")
#         if isinstance(request_params, dict) and "data_refresh_status" in request_params:
#             errors.append("request_params should not contain data_refresh_status")

#         cls = data.get("classification") or {}
#         for key in ["predicted_label", "prob_up", "prob_neutral", "prob_down", "predicted_growth_prob"]:
#             if key not in cls:
#                 errors.append(f"classification missing {key}")

#         try:
#             prob_sum = float(cls.get("prob_up", 0)) + float(cls.get("prob_neutral", 0)) + float(cls.get("prob_down", 0))
#             if not math.isclose(prob_sum, 1.0, rel_tol=1e-3, abs_tol=1e-3):
#                 errors.append(f"classification probabilities sum to {prob_sum}, not close to 1")
#         except Exception:
#             errors.append("classification probabilities are not numeric")

#         reg = data.get("regression") or {}
#         path = reg.get("price_path")
#         if not isinstance(path, list):
#             errors.append("regression.price_path should be a list")
#         else:
#             expected_len = int(data.get("forecast_days") or self.forecast_days)
#             if len(path) != expected_len:
#                 errors.append(f"price_path length {len(path)} != forecast_days {expected_len}")

#         refresh = data.get("data_refresh_status") or {}
#         if not isinstance(refresh, dict):
#             errors.append("data_refresh_status should be an object")
#         else:
#             for key in ["status", "ticker", "can_continue"]:
#                 if key not in refresh:
#                     errors.append(f"data_refresh_status missing {key}")

#         news = data.get("news_summary") or {}
#         if isinstance(news, dict):
#             if not news.get("news_start_time"):
#                 errors.append("news_summary.news_start_time is missing")
#             if not news.get("news_end_time"):
#                 errors.append("news_summary.news_end_time is missing")
#         else:
#             errors.append("news_summary should be an object")

#         return errors

#     def write_reports(self) -> None:
#         ts = now_str()
#         json_path = self.output_dir / f"finsight_api_test_report_{ts}.json"
#         md_path = self.output_dir / f"finsight_api_test_report_{ts}.md"

#         records = [
#             {
#                 "name": r.name,
#                 "method": r.method,
#                 "url": r.url,
#                 "expected_status": r.expected_status,
#                 "request_headers": r.request_headers,
#                 "request_params": r.request_params,
#                 "request_json": r.request_json,
#                 "status_code": r.status_code,
#                 "elapsed_ms": r.elapsed_ms,
#                 "ok": r.ok,
#                 "error": r.error,
#                 "validation_errors": r.validation_errors,
#                 "response": safe_json(r.response),
#             }
#             for r in self.records
#         ]

#         summary = {
#             "base_url": self.base_url,
#             "generated_at": datetime.now(timezone.utc).isoformat(),
#             "total": len(self.records),
#             "passed": sum(1 for r in self.records if r.ok),
#             "failed": sum(1 for r in self.records if not r.ok),
#         }

#         json_path.write_text(json.dumps({"summary": summary, "records": records}, ensure_ascii=False, indent=2), encoding="utf-8")

#         lines: list[str] = []
#         lines.append("# Finsight API 自动化测试报告")
#         lines.append("")
#         lines.append(f"- Base URL: `{self.base_url}`")
#         lines.append(f"- Generated At: `{summary['generated_at']}`")
#         lines.append(f"- Total: **{summary['total']}**")
#         lines.append(f"- Passed: **{summary['passed']}**")
#         lines.append(f"- Failed: **{summary['failed']}**")
#         lines.append("")
#         lines.append("## 汇总表")
#         lines.append("")
#         lines.append("| # | 结果 | 接口 | 方法 | 状态码 | 耗时 ms | 校验错误 |")
#         lines.append("|---:|---|---|---|---:|---:|---:|")
#         for idx, r in enumerate(self.records, 1):
#             result = "✅ PASS" if r.ok else "❌ FAIL"
#             lines.append(f"| {idx} | {result} | {r.name} | {r.method} | {r.status_code} | {r.elapsed_ms} | {len(r.validation_errors)} |")

#         lines.append("")
#         lines.append("## 详细请求与返回")
#         for idx, r in enumerate(self.records, 1):
#             result = "PASS" if r.ok else "FAIL"
#             lines.append("")
#             lines.append(f"### {idx}. {r.name} - {result}")
#             lines.append("")
#             lines.append(f"- Method: `{r.method}`")
#             lines.append(f"- URL: `{r.url}`")
#             lines.append(f"- Expected Status: `{r.expected_status}`")
#             lines.append(f"- Actual Status: `{r.status_code}`")
#             lines.append(f"- Elapsed: `{r.elapsed_ms} ms`")
#             if r.error:
#                 lines.append(f"- Error: `{r.error}`")
#             if r.validation_errors:
#                 lines.append("- Validation Errors:")
#                 for err in r.validation_errors:
#                     lines.append(f"  - `{err}`")

#             lines.append("")
#             lines.append("**Request Headers**")
#             lines.append("```json")
#             lines.append(pretty(r.request_headers))
#             lines.append("```")

#             if r.request_params is not None:
#                 lines.append("**Request Params**")
#                 lines.append("```json")
#                 lines.append(pretty(r.request_params))
#                 lines.append("```")

#             if r.request_json is not None:
#                 lines.append("**Request JSON**")
#                 lines.append("```json")
#                 lines.append(pretty(r.request_json))
#                 lines.append("```")

#             lines.append("**Response**")
#             lines.append("```json")
#             lines.append(pretty(r.response))
#             lines.append("```")

#         md_path.write_text("\n".join(lines), encoding="utf-8")

#         print("\n测试完成：")
#         print(f"  JSON 报告：{json_path}")
#         print(f"  Markdown 报告：{md_path}")
#         print(f"  Total={summary['total']} Passed={summary['passed']} Failed={summary['failed']}")


# def parse_args() -> argparse.Namespace:
#     parser = argparse.ArgumentParser(description="Finsight API 自动化测试脚本 v1.3")
#     parser.add_argument("--base-url", default="http://127.0.0.1:8002", help="后端服务地址")
#     parser.add_argument("--admin-user", default="admin", help="管理员用户名")
#     parser.add_argument("--admin-pass", default="Admin123", help="管理员密码")
#     parser.add_argument("--user", default="user01", help="普通用户用户名")
#     parser.add_argument("--user-pass", default="User123", help="普通用户密码")
#     parser.add_argument("--output-dir", default="api_test_results", help="测试报告输出目录")
#     parser.add_argument("--timeout", type=int, default=30, help="单个请求超时时间，秒")

#     parser.add_argument("--prediction-ticker", default="GOOGL", help="预测测试 ticker")
#     parser.add_argument("--prediction-base-date", default="2026-05-29", help="预测基准日")
#     parser.add_argument("--forecast-days", type=int, default=5, help="预测天数，当前建议 1~5")

#     parser.add_argument("--pipeline-ticker", default="GOOGL", help="Data Pipeline 测试 ticker")
#     parser.add_argument("--pipeline-target-date", default="2026-05-29", help="Data Pipeline 目标日期")
#     parser.add_argument("--pipeline-modules", default="market,technical,news,sentiment,features", help="Data Pipeline 模块列表")
#     parser.add_argument("--run-data-pipeline", action="store_true", help="是否执行 Data Pipeline job，默认只查 coverage")

#     parser.add_argument("--run-daily-refresh", action="store_true", help="是否测试每日补全兼容接口")
#     parser.add_argument("--daily-refresh-target-date", default="2026-05-29", help="每日补全目标日期")

#     parser.add_argument("--run-on-demand-prediction", action="store_true", help="是否额外测试一个 on-demand 预测 ticker")
#     parser.add_argument("--on-demand-ticker", default="META", help="on-demand 预测测试 ticker")

#     parser.add_argument("--strict-prediction-checks", action="store_true", help="on-demand 预测也严格要求 200 和结构校验")
#     return parser.parse_args()


# def main() -> None:
#     args = parse_args()
#     tester = FinsightApiTester(
#         base_url=args.base_url,
#         admin_user=args.admin_user,
#         admin_pass=args.admin_pass,
#         normal_user=args.user,
#         normal_pass=args.user_pass,
#         output_dir=args.output_dir,
#         timeout=args.timeout,
#         prediction_ticker=args.prediction_ticker,
#         prediction_base_date=args.prediction_base_date,
#         forecast_days=args.forecast_days,
#         pipeline_ticker=args.pipeline_ticker,
#         pipeline_target_date=args.pipeline_target_date,
#         pipeline_modules=[x.strip() for x in args.pipeline_modules.split(",") if x.strip()],
#         run_data_pipeline=args.run_data_pipeline,
#         run_daily_refresh=args.run_daily_refresh,
#         daily_refresh_target_date=args.daily_refresh_target_date,
#         run_on_demand_prediction=args.run_on_demand_prediction,
#         on_demand_ticker=args.on_demand_ticker,
#         strict_prediction_checks=args.strict_prediction_checks,
#     )
#     tester.run_all()


# if __name__ == "__main__":
#     main()
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Finsight API 自动化测试脚本 v1.4.1-current-backend

用途：
1. 自动登录普通用户和管理员，自动携带 JWT Token。
2. 按当前 Finsight v1.3/v1.4 后端能力测试 API：
   - health
   - auth login / me / 可选 register
   - stocks search / detail / news list / news detail / sentiment summary
   - watchlist add / list / delete
   - models active
   - data-pipeline coverage / 可选 jobs
   - predictions run / history / detail
   - backtest run / status / frames / day detail / logs / summary / final positions
   - crawler status / daily-refresh status / 可选 daily-refresh run / 可选 stock-universe sync
   - logs
   - admin users list / detail / 可选 CRUD
3. 输出 JSON 与 Markdown 两份测试报告。
4. 增强结构校验：
   - 股票详情 sentiment_counts 必须包含 14 天窗口、正负中性新闻数、起止日期；
   - 新闻列表 pagination 与 sentiment_counts 必须存在；
   - 新闻详情兼容当前后端字段：content_status 必须存在，has_original_content / detail_source 若存在则校验类型；
   - 情绪摘要必须包含 sentiment_counts，并检查总数一致性；
   - 当前未应用 30 天预测补丁时，默认只测试 forecast_days=5；若手动传 >5，会按扩展预测字段校验；
   - 预测历史与详情通过 prediction_id 串联；
   - 回测接口覆盖 /days/{date} 与 /final-positions。

基础运行：
    python finsight_api_auto_test_v1_4.py --base-url http://127.0.0.1:8002

推荐完整但相对安全的运行：
    python finsight_api_auto_test_v1_4.py \
      --base-url http://127.0.0.1:8002 \
      --forecast-days 5 \
      --run-auth-register \
      --run-admin-crud

包含较重/外部数据任务的运行：
    python finsight_api_auto_test_v1_4.py \
      --base-url http://127.0.0.1:8002 \
      --forecast-days 5 \
      --run-auth-register \
      --run-admin-crud \
      --run-data-pipeline \
      --run-daily-refresh \
      --run-stock-universe-sync

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

NATIVE_MODEL_FORECAST_DAYS = 5
MAX_FORECAST_DAYS = 30


def now_str() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def mask_sensitive(obj: Any) -> Any:
    if isinstance(obj, dict):
        sensitive_lower = {x.lower() for x in SENSITIVE_KEYS}
        result = {}
        for k, v in obj.items():
            if str(k) in SENSITIVE_KEYS or str(k).lower() in sensitive_lower:
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


def as_data(body: Any) -> dict[str, Any]:
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        return body["data"]
    return {}


def is_success_body(body: Any) -> bool:
    return isinstance(body, dict) and body.get("success") is True and isinstance(body.get("data"), dict)


def add_required(errors: list[str], obj: dict[str, Any], fields: list[str], prefix: str) -> None:
    for field_name in fields:
        if field_name not in obj:
            errors.append(f"{prefix} missing {field_name}")


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
        news_window_days: int,
        pipeline_ticker: str,
        pipeline_target_date: str | None,
        pipeline_modules: list[str],
        run_data_pipeline: bool,
        run_daily_refresh: bool,
        daily_refresh_target_date: str | None,
        run_stock_universe_sync: bool,
        run_auth_register: bool,
        run_admin_crud: bool,
        run_on_demand_prediction: bool,
        on_demand_ticker: str,
        run_intraday_check: bool,
        run_news_force_fetch_check: bool,
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
        self.news_window_days = news_window_days

        self.pipeline_ticker = pipeline_ticker.upper()
        self.pipeline_target_date = pipeline_target_date
        self.pipeline_modules = pipeline_modules
        self.run_data_pipeline = run_data_pipeline
        self.run_daily_refresh = run_daily_refresh
        self.daily_refresh_target_date = daily_refresh_target_date
        self.run_stock_universe_sync = run_stock_universe_sync

        self.run_auth_register = run_auth_register
        self.run_admin_crud = run_admin_crud
        self.run_on_demand_prediction = run_on_demand_prediction
        self.on_demand_ticker = on_demand_ticker.upper()
        self.run_intraday_check = run_intraday_check
        self.run_news_force_fetch_check = run_news_force_fetch_check
        self.strict_prediction_checks = strict_prediction_checks

        self.records: list[TestRecord] = []
        self.admin_token: Optional[str] = None
        self.user_token: Optional[str] = None
        self.created_prediction_id: Optional[int] = None
        self.created_backtest_run_id: Optional[int] = None
        self.created_backtest_start_date: Optional[str] = None
        self.created_temp_user_id: Optional[int] = None
        self.created_temp_username: Optional[str] = None
        self.created_temp_password: str = "TempUser123"
        self.first_news_id: Optional[int] = None

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

            # Only validate successful 2xx response bodies.
            # Some tests intentionally accept 404/403 for not-ready or permission paths;
            # those status checks should pass without forcing success-body validation.
            if record.ok and validator is not None and response.status_code is not None and 200 <= response.status_code < 300:
                record.validation_errors = validator(record.response)
                if record.validation_errors:
                    record.ok = False

        except Exception as exc:
            record.elapsed_ms = round((time.time() - start) * 1000, 2)
            record.error = repr(exc)
            record.ok = False

        self.records.append(record)
        status = record.status_code if record.status_code is not None else "ERR"
        suffix = f" validation_errors={len(record.validation_errors)}" if record.validation_errors else ""
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

    # =========================
    # Test groups
    # =========================

    def test_health(self) -> None:
        self.request("Health Check", "GET", "/health", expected_status=[200], validator=self.validate_success_data)

    def test_auth(self) -> None:
        resp, _ = self.request(
            "Auth - Login Normal User",
            "POST",
            "/api/auth/login",
            json_body={"username": self.normal_user, "password": self.normal_pass},
            expected_status=[200],
            validator=self.validate_login_response,
        )
        self.user_token = self.get_data(resp).get("token")

        resp, _ = self.request(
            "Auth - Login Admin",
            "POST",
            "/api/auth/login",
            json_body={"username": self.admin_user, "password": self.admin_pass},
            expected_status=[200],
            validator=self.validate_login_response,
        )
        self.admin_token = self.get_data(resp).get("token")

        self.request("Auth - Me Normal", "GET", "/api/auth/me", token=self.user_token, expected_status=[200], validator=self.validate_me_response)
        self.request("Auth - Me Admin", "GET", "/api/auth/me", token=self.admin_token, expected_status=[200], validator=self.validate_me_response)

        if self.run_auth_register or self.run_admin_crud:
            username = f"api_test_{utc_compact()}"
            resp, _ = self.request(
                "Auth - Register Temp User",
                "POST",
                "/api/auth/register",
                json_body={
                    "username": username,
                    "password": self.created_temp_password,
                    "confirm_password": self.created_temp_password,
                },
                expected_status=[200, 400],
                validator=self.validate_register_response,
            )
            data = self.get_data(resp)
            if data.get("user_id"):
                self.created_temp_user_id = int(data["user_id"])
                self.created_temp_username = str(data.get("username") or username)
                temp_resp, _ = self.request(
                    "Auth - Login Temp User",
                    "POST",
                    "/api/auth/login",
                    json_body={"username": self.created_temp_username, "password": self.created_temp_password},
                    expected_status=[200],
                    validator=self.validate_login_response,
                )
                temp_token = self.get_data(temp_resp).get("token")
                self.request("Auth - Me Temp User", "GET", "/api/auth/me", token=temp_token, expected_status=[200], validator=self.validate_me_response)

    def test_stock_api(self) -> None:
        ticker = self.prediction_ticker

        resp, _ = self.request(
            f"Stock - Search {ticker}",
            "GET",
            "/api/stocks/search",
            token=self.user_token,
            params={"keyword": ticker, "only_supported": False, "include_etf": True, "limit": 10},
            expected_status=[200],
            validator=self.validate_stock_search,
        )
        items = self.get_items(resp)
        if items:
            ticker = items[0].get("ticker") or ticker

        self.request(
            "Stock - Detail 1m",
            "GET",
            f"/api/stocks/{ticker}/detail",
            token=self.user_token,
            params={"range": "1m", "include_news": True, "include_indicators": True, "auto_refresh": False},
            expected_status=[200, 404],
            validator=self.validate_stock_detail,
        )

        self.request(
            "Stock - Detail all",
            "GET",
            f"/api/stocks/{ticker}/detail",
            token=self.user_token,
            params={"range": "all", "include_news": False, "include_indicators": False, "auto_refresh": False},
            expected_status=[200, 404],
            validator=self.validate_stock_detail_light,
        )

        if self.run_intraday_check:
            self.request(
                "Stock - Detail 1d Intraday",
                "GET",
                f"/api/stocks/{ticker}/detail",
                token=self.user_token,
                params={"range": "1d", "include_news": False, "include_indicators": False, "auto_refresh": False},
                expected_status=[200, 404, 500],
                validator=None,
            )

        news_resp, _ = self.request(
            "Stock - News List",
            "GET",
            f"/api/stocks/{ticker}/news",
            token=self.user_token,
            params={"limit": 5, "cursor": 0},
            expected_status=[200, 404],
            validator=self.validate_news_list,
        )
        news_items = self.get_items(news_resp)
        if news_items:
            news_id = news_items[0].get("news_id") or news_items[0].get("id")
            if news_id is not None:
                self.first_news_id = int(news_id)
                self.request(
                    "Stock - News Detail",
                    "GET",
                    f"/api/stocks/news/{news_id}",
                    token=self.user_token,
                    params={"include_html": False, "fetch_missing": False},
                    expected_status=[200, 404],
                    validator=self.validate_news_detail,
                )
                if self.run_news_force_fetch_check:
                    self.request(
                        "Stock - News Detail Force Fetch",
                        "GET",
                        f"/api/stocks/news/{news_id}",
                        token=self.user_token,
                        params={"include_html": False, "fetch_missing": True, "force_fetch": True},
                        expected_status=[200, 404, 500],
                        validator=None,
                    )

        self.request(
            "Stock - Sentiment Summary 14d",
            "GET",
            f"/api/stocks/{ticker}/sentiment-summary",
            token=self.user_token,
            params={"window_days": 14},
            expected_status=[200, 404],
            validator=self.validate_sentiment_summary,
        )

        self.request(
            "Stock - News List Positive Filter",
            "GET",
            f"/api/stocks/{ticker}/news",
            token=self.user_token,
            params={"limit": 3, "sentiment_label": "positive"},
            expected_status=[200, 404],
            validator=self.validate_news_list,
        )

    def test_watchlist_api(self) -> None:
        ticker = self.prediction_ticker
        self.request(
            f"Watchlist - Add {ticker}",
            "POST",
            "/api/watchlist",
            token=self.user_token,
            json_body={"ticker": ticker, "auto_fetch": False},
            expected_status=[200, 400, 404, 409],
            validator=self.validate_watchlist_add,
        )
        self.request(
            "Watchlist - List With Curve",
            "GET",
            "/api/watchlist",
            token=self.user_token,
            params={"include_curve": True},
            expected_status=[200],
            validator=self.validate_watchlist_list,
        )
        self.request(
            "Watchlist - List Without Curve",
            "GET",
            "/api/watchlist",
            token=self.user_token,
            params={"include_curve": False},
            expected_status=[200],
            validator=self.validate_watchlist_list,
        )
        self.request(
            f"Watchlist - Delete {ticker}",
            "DELETE",
            f"/api/watchlist/{ticker}",
            token=self.user_token,
            expected_status=[200, 404],
            validator=self.validate_watchlist_delete,
        )

    def test_model_api(self) -> None:
        self.request(
            "Model - Active Models",
            "GET",
            "/api/models/active",
            token=self.user_token,
            expected_status=[200, 404],
            validator=self.validate_active_models,
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
            "news_window_days": self.news_window_days,
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
                "news_window_days": self.news_window_days,
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
            validator=self.validate_prediction_history,
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

        # 用一个通常不存在的 prediction_id 检查权限/不存在路径不会 500。
        self.request(
            "Prediction - Detail Not Found",
            "GET",
            "/api/predictions/999999999",
            token=self.user_token,
            expected_status=[404],
            validator=None,
        )

    def test_backtest_api(self) -> None:
        start_date = "2026-05-01"
        end_date = "2026-05-29"
        resp, _ = self.request(
            "Backtest - Run",
            "POST",
            "/api/backtest/run",
            token=self.user_token,
            json_body={
                "run_name": "API Auto Test Backtest",
                "tickers": [self.prediction_ticker],
                "start_date": start_date,
                "end_date": end_date,
                "initial_cash": 10000,
                "forecast_days": min(self.forecast_days, MAX_FORECAST_DAYS),
                "max_position_ratio": 0.3,
                "max_holding_count": 3,
                "fee_rate": 0.0005,
                "benchmark": "SPY",
                "save_daily_positions": True,
                "save_event_logs": True,
                "animation_mode": "realtime",
            },
            expected_status=[200, 400, 404, 500],
            validator=self.validate_backtest_run,
        )
        data = self.get_data(resp)
        self.created_backtest_run_id = data.get("run_id") or data.get("id")
        self.created_backtest_start_date = data.get("start_date") or start_date

        if not self.created_backtest_run_id:
            return

        run_id = self.created_backtest_run_id
        self.request("Backtest - Status", "GET", f"/api/backtest/{run_id}/status", token=self.user_token, expected_status=[200, 404], validator=self.validate_backtest_status)
        self.request(
            "Backtest - Frames",
            "GET",
            f"/api/backtest/{run_id}/frames",
            token=self.user_token,
            params={"limit": 3, "include_positions": True, "include_position_curves": False},
            expected_status=[200, 404],
            validator=self.validate_backtest_frames,
        )
        self.request(
            "Backtest - Day Detail",
            "GET",
            f"/api/backtest/{run_id}/days/{self.created_backtest_start_date}",
            token=self.user_token,
            expected_status=[200, 404],
            validator=self.validate_backtest_day_detail,
        )
        self.request("Backtest - Logs", "GET", f"/api/backtest/{run_id}/logs", token=self.user_token, params={"limit": 20}, expected_status=[200, 404], validator=self.validate_backtest_logs)
        self.request("Backtest - Summary", "GET", f"/api/backtest/{run_id}/summary", token=self.user_token, expected_status=[200, 404], validator=self.validate_backtest_summary)
        self.request("Backtest - Final Positions By Run", "GET", f"/api/backtest/{run_id}/final-positions", token=self.user_token, expected_status=[200, 404], validator=self.validate_final_positions)
        self.request("Backtest - Latest Final Positions", "GET", "/api/backtest/latest/final-positions", token=self.user_token, params={"include_empty": True}, expected_status=[200, 404], validator=self.validate_final_positions)

    def test_crawler_api(self) -> None:
        self.request("Crawler - Status", "GET", "/api/crawler/status", token=self.admin_token, expected_status=[200, 403, 404], validator=self.validate_crawler_status)
        self.request("Crawler - Stock Universe Status", "GET", "/api/crawler/stock-universe/status", token=self.admin_token, expected_status=[200, 403, 404], validator=self.validate_stock_universe_status)

        if self.run_stock_universe_sync:
            self.request(
                "Crawler - Stock Universe Sync",
                "POST",
                "/api/crawler/stock-universe/sync",
                token=self.admin_token,
                json_body={"force": False},
                expected_status=[200, 400, 403, 404, 500],
                validator=None,
            )

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
                validator=None,
            )

        self.request("Crawler - Daily Refresh Status", "GET", "/api/crawler/daily-refresh/status", token=self.admin_token, expected_status=[200, 403, 404], validator=self.validate_daily_refresh_status)

    def test_log_api(self) -> None:
        self.request("Log - Query Logs", "GET", "/api/logs", token=self.admin_token, params={"page": 1, "page_size": 20}, expected_status=[200, 403], validator=self.validate_logs)
        self.request("Log - Query Prediction Logs", "GET", "/api/logs", token=self.admin_token, params={"module": "PredictionService", "page": 1, "page_size": 5}, expected_status=[200, 403], validator=self.validate_logs)

    def test_admin_user_api(self) -> None:
        list_resp, _ = self.request("Admin - User List", "GET", "/api/admin/users", token=self.admin_token, params={"page": 1, "page_size": 20}, expected_status=[200, 403], validator=self.validate_admin_users_list)
        data = self.get_data(list_resp)
        items = data.get("items") if isinstance(data.get("items"), list) else []
        target_id = self.created_temp_user_id
        if target_id is None and items:
            target_id = items[0].get("user_id")

        if target_id:
            self.request("Admin - User Detail", "GET", f"/api/admin/users/{target_id}", token=self.admin_token, expected_status=[200, 403, 404], validator=self.validate_admin_user_detail)

        if self.run_admin_crud and self.created_temp_user_id:
            uid = self.created_temp_user_id
            new_username = f"{self.created_temp_username}_renamed"
            self.request(
                "Admin - Update Temp User Status Disabled",
                "PUT",
                f"/api/admin/users/{uid}/status",
                token=self.admin_token,
                json_body={"status": "disabled", "reason": "api auto test"},
                expected_status=[200, 400, 403, 404],
                validator=self.validate_admin_update_status,
            )
            self.request(
                "Admin - Update Temp User Status Active",
                "PUT",
                f"/api/admin/users/{uid}/status",
                token=self.admin_token,
                json_body={"status": "active", "reason": "api auto test restore"},
                expected_status=[200, 400, 403, 404],
                validator=self.validate_admin_update_status,
            )
            self.request(
                "Admin - Update Temp User Role User",
                "PUT",
                f"/api/admin/users/{uid}/role",
                token=self.admin_token,
                json_body={"role": "user", "reason": "api auto test"},
                expected_status=[200, 400, 403, 404],
                validator=self.validate_admin_update_role,
            )
            self.request(
                "Admin - Update Temp Username",
                "PUT",
                f"/api/admin/users/{uid}/username",
                token=self.admin_token,
                json_body={"username": new_username, "reason": "api auto test"},
                expected_status=[200, 400, 403, 404],
                validator=self.validate_admin_update_username,
            )
            self.created_temp_username = new_username
            self.request(
                "Admin - Reset Temp User Password",
                "PUT",
                f"/api/admin/users/{uid}/password",
                token=self.admin_token,
                json_body={
                    "new_password": "TempUser456",
                    "confirm_password": "TempUser456",
                    "force_logout": True,
                    "reason": "api auto test",
                },
                expected_status=[200, 400, 403, 404],
                validator=self.validate_admin_reset_password,
            )
            self.request(
                "Admin - Delete Temp User",
                "DELETE",
                f"/api/admin/users/{uid}",
                token=self.admin_token,
                json_body={"reason": "api auto test cleanup"},
                expected_status=[200, 400, 403, 404],
                validator=self.validate_admin_delete_user,
            )

    # =========================
    # Validators
    # =========================

    def validate_success_data(self, body: Any) -> list[str]:
        return [] if is_success_body(body) else ["response should be success=true and data object"]

    def validate_login_response(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        add_required(errors, data, ["token", "user_id", "username", "role", "status"], "login.data")
        return errors

    def validate_register_response(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        add_required(errors, data, ["user_id", "username", "role", "status", "created_at"], "register.data")
        return errors

    def validate_me_response(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        add_required(errors, data, ["user_id", "username", "role", "status"], "me.data")
        return errors

    def validate_stock_search(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        if not isinstance(data.get("items"), list):
            errors.append("stock search items should be a list")
        if "total" not in data:
            errors.append("stock search missing total")
        return errors

    def validate_stock_detail(self, body: Any) -> list[str]:
        errors = self.validate_stock_detail_light(body)
        data = as_data(body)
        if not isinstance(data.get("current_quote"), dict):
            errors.append("stock detail current_quote should be an object")
        else:
            quote = data["current_quote"]
            add_required(errors, quote, ["daily_return", "amplitude", "fifty_two_week_high", "fifty_two_week_low", "change_percent"], "current_quote")
        if not isinstance(data.get("price_curve"), list):
            errors.append("stock detail price_curve should be a list")
        if not isinstance(data.get("indicator_curve"), list):
            errors.append("stock detail indicator_curve should be a list")
        if not isinstance(data.get("latest_news"), list):
            errors.append("stock detail latest_news should be a list")
        return errors

    def validate_stock_detail_light(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        required_top = [
            "ticker", "company_name", "market", "is_supported", "raw_is_supported", "data_status",
            "price_range", "data_frequency", "price_curve_count", "price_curve_start", "price_curve_end",
            "current_quote", "price_curve", "sentiment_counts", "sentiment_summary",
        ]
        add_required(errors, data, required_top, "stock detail")
        errors.extend(self._validate_sentiment_counts(data.get("sentiment_counts"), "stock detail sentiment_counts"))
        return errors

    def validate_news_list(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        required = ["ticker", "return_all", "pagination_mode", "cursor", "next_cursor", "has_more", "returned_count", "sentiment_counts", "total", "items"]
        add_required(errors, data, required, "news list")
        if not isinstance(data.get("items"), list):
            errors.append("news list items should be a list")
        if isinstance(data.get("items"), list):
            for idx, item in enumerate(data["items"][:3]):
                if not isinstance(item, dict):
                    errors.append(f"news list item {idx} should be object")
                    continue
                add_required(errors, item, ["news_id", "title", "summary", "source", "url", "publish_time", "sentiment_score", "sentiment_label", "has_detail"], f"news item {idx}")
                # has_original_content / content_status 是新闻原文补丁后的增强字段。
                # 当前未应用该补丁的后端不会返回它们，因此这里只在字段存在时做类型校验。
                if "has_original_content" in item and not isinstance(item.get("has_original_content"), bool):
                    errors.append(f"news item {idx} has_original_content should be bool when present")
        errors.extend(self._validate_sentiment_counts(data.get("sentiment_counts"), "news list sentiment_counts"))
        return errors

    def validate_news_detail(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        required = [
            "news_id", "ticker", "title", "summary", "content_text", "content_html",
            "source", "url", "publish_time", "sentiment_score", "sentiment_label",
            "news_llm_analysis", "content_status", "content_fetched_at",
        ]
        add_required(errors, data, required, "news detail")
        # has_original_content / detail_source 是新闻原文补丁后的增强字段。
        # 当前后端未应用该补丁时可以不存在；如果存在，则校验类型。
        if "has_original_content" in data and not isinstance(data.get("has_original_content"), bool):
            errors.append("news detail has_original_content should be bool when present")
        if "detail_source" in data and data.get("detail_source") is not None and not isinstance(data.get("detail_source"), str):
            errors.append("news detail detail_source should be string or null when present")
        return errors

    def validate_sentiment_summary(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        required = [
            "ticker", "sentiment_window_days", "sentiment_start_date", "sentiment_end_date", "sentiment_score",
            "sentiment_label", "positive_news_count", "negative_news_count", "neutral_news_count", "total_news_count",
            "sentiment_curve", "sentiment_counts",
        ]
        add_required(errors, data, required, "sentiment summary")
        if not isinstance(data.get("sentiment_curve"), list):
            errors.append("sentiment summary sentiment_curve should be a list")
        errors.extend(self._validate_sentiment_counts(data.get("sentiment_counts"), "sentiment summary sentiment_counts"))
        return errors

    def validate_watchlist_add(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        add_required(errors, data, ["ticker", "company_name", "is_supported", "added_at"], "watchlist add")
        return errors

    def validate_watchlist_list(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        if not isinstance(data.get("items"), list):
            errors.append("watchlist items should be a list")
        return errors

    def validate_watchlist_delete(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        add_required(errors, data, ["ticker", "deleted"], "watchlist delete")
        return errors

    def validate_active_models(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        for key in ["classifier", "aux_classifier", "regressor"]:
            if key not in data:
                errors.append(f"active models missing {key}")
        return errors

    def validate_data_pipeline_coverage(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        for key in ["price_data", "technical_indicators", "news_data", "sentiment_daily", "model_feature_snapshots", "recommendation"]:
            if key not in data:
                errors.append(f"coverage missing {key}")
        return errors

    def validate_data_pipeline_job(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        for key in ["job_id", "status", "tickers", "modules", "items"]:
            if key not in data:
                errors.append(f"pipeline job missing {key}")
        return errors

    def validate_prediction_response(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        required_top = [
            "prediction_id", "ticker", "base_trading_date", "forecast_start_date", "forecast_end_date",
            "forecast_days", "model_version", "reg_model_version", "request_params", "classification",
            "regression", "data_refresh_status", "news_summary", "explanations", "llm_report",
        ]
        add_required(errors, data, required_top, "prediction")

        request_params = data.get("request_params")
        if isinstance(request_params, dict) and "data_refresh_status" in request_params:
            errors.append("request_params should not contain data_refresh_status")

        cls = data.get("classification") or {}
        if isinstance(cls, dict):
            add_required(errors, cls, ["predicted_label", "prob_up", "prob_neutral", "prob_down", "predicted_growth_prob"], "classification")
            try:
                prob_sum = float(cls.get("prob_up", 0)) + float(cls.get("prob_neutral", 0)) + float(cls.get("prob_down", 0))
                if not math.isclose(prob_sum, 1.0, rel_tol=1e-3, abs_tol=1e-3):
                    errors.append(f"classification probabilities sum to {prob_sum}, not close to 1")
            except Exception:
                errors.append("classification probabilities are not numeric")
        else:
            errors.append("classification should be an object")

        reg = data.get("regression") or {}
        if isinstance(reg, dict):
            path = reg.get("price_path")
            if not isinstance(path, list):
                errors.append("regression.price_path should be a list")
            else:
                expected_len = int(data.get("forecast_days") or self.forecast_days)
                if len(path) != expected_len:
                    errors.append(f"price_path length {len(path)} != forecast_days {expected_len}")
                for idx, point in enumerate(path[: min(3, len(path))]):
                    if isinstance(point, dict):
                        add_required(errors, point, ["day_index", "target_date", "predicted_price", "predicted_return"], f"price_path[{idx}]")
                    else:
                        errors.append(f"price_path[{idx}] should be object")
        else:
            errors.append("regression should be an object")

        forecast_days = data.get("forecast_days")
        if isinstance(forecast_days, int) and forecast_days > NATIVE_MODEL_FORECAST_DAYS:
            fg = data.get("forecast_generation")
            if not isinstance(fg, dict):
                errors.append("forecast_days > 5 should include forecast_generation")
            else:
                add_required(errors, fg, ["requested_forecast_days", "returned_forecast_days", "native_model_forecast_days", "extension_days", "method"], "forecast_generation")

        refresh = data.get("data_refresh_status") or {}
        if not isinstance(refresh, dict):
            errors.append("data_refresh_status should be an object")
        else:
            add_required(errors, refresh, ["status", "ticker", "can_continue"], "data_refresh_status")

        news = data.get("news_summary") or {}
        if isinstance(news, dict):
            for key in ["news_start_time", "news_end_time", "positive_news_count", "negative_news_count", "neutral_news_count", "total_news_count"]:
                if key not in news or news.get(key) is None:
                    errors.append(f"news_summary.{key} is missing")
        else:
            errors.append("news_summary should be an object")

        return errors

    def validate_prediction_history(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        add_required(errors, data, ["items", "total", "page", "page_size"], "prediction history")
        if not isinstance(data.get("items"), list):
            errors.append("prediction history items should be a list")
        else:
            for idx, item in enumerate(data["items"][:3]):
                if isinstance(item, dict):
                    add_required(errors, item, ["prediction_id", "ticker", "prediction_time", "base_trading_date", "forecast_days", "predicted_label", "prob_up", "prob_down", "news_start_time", "news_end_time"], f"prediction history item {idx}")
                else:
                    errors.append(f"prediction history item {idx} should be object")
        return errors

    def validate_backtest_run(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        add_required(errors, data, ["run_id", "run_name", "status", "start_date", "end_date", "created_at", "polling"], "backtest run")
        if isinstance(data.get("polling"), dict):
            add_required(errors, data["polling"], ["status_url", "frames_url", "logs_url", "final_positions_url"], "backtest polling")
        else:
            errors.append("backtest polling should be object")
        return errors

    def validate_backtest_status(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        add_required(errors, data, ["run_id", "status", "start_date", "end_date", "trading_days_done", "progress", "final_positions_ready", "error_message"], "backtest status")
        return errors

    def validate_backtest_frames(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        add_required(errors, data, ["run_id", "status", "frames", "next_after_date", "has_more"], "backtest frames")
        if not isinstance(data.get("frames"), list):
            errors.append("backtest frames should be list")
        return errors

    def validate_backtest_day_detail(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        # 当前回测还是 stub 时，day detail 可能是空壳，但不应缺 data 对象。
        if not data:
            errors.append("backtest day detail data is empty")
        return errors

    def validate_backtest_logs(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        add_required(errors, data, ["run_id", "items", "next_after_log_id", "has_more"], "backtest logs")
        if not isinstance(data.get("items"), list):
            errors.append("backtest logs items should be list")
        return errors

    def validate_backtest_summary(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        add_required(errors, data, ["run_id", "run_name", "status", "start_date", "end_date", "initial_cash", "benchmark"], "backtest summary")
        return errors

    def validate_final_positions(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        if "positions" not in data or not isinstance(data.get("positions"), list):
            errors.append("final positions positions should be a list")
        return errors

    def validate_crawler_status(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        if not isinstance(data.get("latest_tasks"), list):
            errors.append("crawler status latest_tasks should be a list")
        if "missing_data_summary" not in data:
            errors.append("crawler status missing missing_data_summary")
        return errors

    def validate_stock_universe_status(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        if "latest_sync" not in data:
            errors.append("stock universe status missing latest_sync")
        if not isinstance(data.get("source_files"), list):
            errors.append("stock universe source_files should be list")
        return errors

    def validate_daily_refresh_status(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        if "latest_batch" not in data:
            errors.append("daily refresh status missing latest_batch")
        if not isinstance(data.get("recent_ticker_tasks"), list):
            errors.append("daily refresh recent_ticker_tasks should be list")
        return errors

    def validate_logs(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        add_required(errors, data, ["items", "total", "page", "page_size"], "logs")
        if not isinstance(data.get("items"), list):
            errors.append("logs items should be list")
        return errors

    def validate_admin_users_list(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        add_required(errors, data, ["items", "total", "page", "page_size"], "admin user list")
        if not isinstance(data.get("items"), list):
            errors.append("admin user list items should be list")
        return errors

    def validate_admin_user_detail(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        add_required(errors, data, ["user_id", "username", "role", "status", "created_at", "prediction_count", "backtest_count", "watchlist_count", "recent_operations"], "admin user detail")
        return errors

    def validate_admin_update_status(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        add_required(errors, data, ["user_id", "username", "status", "updated_at"], "admin update status")
        return errors

    def validate_admin_update_role(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        add_required(errors, data, ["user_id", "username", "role", "updated_at"], "admin update role")
        return errors

    def validate_admin_update_username(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        add_required(errors, data, ["user_id", "old_username", "new_username", "updated_at"], "admin update username")
        return errors

    def validate_admin_reset_password(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        add_required(errors, data, ["user_id", "username", "password_updated", "force_logout", "updated_at"], "admin reset password")
        return errors

    def validate_admin_delete_user(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        add_required(errors, data, ["user_id", "username", "deleted", "hard_delete"], "admin delete user")
        return errors

    def _validate_sentiment_counts(self, counts: Any, prefix: str) -> list[str]:
        errors: list[str] = []
        if not isinstance(counts, dict):
            return [f"{prefix} should be an object"]
        required = [
            "window_days", "start_date", "end_date", "news_start_time", "news_end_time", "count_source",
            "positive_news_count", "negative_news_count", "neutral_news_count", "total_news_count",
        ]
        add_required(errors, counts, required, prefix)
        for key in ["positive_news_count", "negative_news_count", "neutral_news_count", "total_news_count"]:
            value = counts.get(key)
            if value is not None and not isinstance(value, int):
                errors.append(f"{prefix}.{key} should be int")
        if counts.get("window_days") != 14:
            errors.append(f"{prefix}.window_days should be 14")
        total = counts.get("total_news_count")
        parts = [counts.get("positive_news_count"), counts.get("negative_news_count"), counts.get("neutral_news_count")]
        if isinstance(total, int) and all(isinstance(x, int) for x in parts):
            if sum(parts) != total:
                errors.append(f"{prefix}: positive+negative+neutral != total")
        return errors

    # =========================
    # Report
    # =========================

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
            "created_prediction_id": self.created_prediction_id,
            "created_backtest_run_id": self.created_backtest_run_id,
            "created_temp_user_id": self.created_temp_user_id,
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
        lines.append(f"- Created Prediction ID: `{summary['created_prediction_id']}`")
        lines.append(f"- Created Backtest Run ID: `{summary['created_backtest_run_id']}`")
        lines.append(f"- Created Temp User ID: `{summary['created_temp_user_id']}`")
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
    parser = argparse.ArgumentParser(description="Finsight API 自动化测试脚本 v1.4")
    parser.add_argument("--base-url", default="http://127.0.0.1:8002", help="后端服务地址")
    parser.add_argument("--admin-user", default="admin", help="管理员用户名")
    parser.add_argument("--admin-pass", default="Admin123", help="管理员密码")
    parser.add_argument("--user", default="user01", help="普通用户用户名")
    parser.add_argument("--user-pass", default="User123", help="普通用户密码")
    parser.add_argument("--output-dir", default="api_test_results", help="测试报告输出目录")
    parser.add_argument("--timeout", type=int, default=30, help="单个请求超时时间，秒")

    parser.add_argument("--prediction-ticker", default="GOOGL", help="预测测试 ticker")
    parser.add_argument("--prediction-base-date", default="2026-05-29", help="预测基准日；为空字符串表示由后端自动选择")
    parser.add_argument("--forecast-days", type=int, default=5, help="预测天数；当前未应用 30 天预测补丁时建议使用 1~5；>5 仅在已应用扩展预测补丁后使用")
    parser.add_argument("--news-window-days", type=int, default=14, help="预测新闻窗口天数")

    parser.add_argument("--pipeline-ticker", default="GOOGL", help="Data Pipeline 测试 ticker")
    parser.add_argument("--pipeline-target-date", default="2026-05-29", help="Data Pipeline 目标日期")
    parser.add_argument("--pipeline-modules", default="market,technical,news,sentiment,features", help="Data Pipeline 模块列表")
    parser.add_argument("--run-data-pipeline", action="store_true", help="是否执行 Data Pipeline job，默认只查 coverage")

    parser.add_argument("--run-daily-refresh", action="store_true", help="是否触发每日补全任务；默认只查状态")
    parser.add_argument("--daily-refresh-target-date", default="2026-05-29", help="每日补全目标日期")
    parser.add_argument("--run-stock-universe-sync", action="store_true", help="是否触发股票基础库同步；该任务会访问外部 Nasdaq 文件")

    parser.add_argument("--run-auth-register", action="store_true", help="是否测试注册临时用户")
    parser.add_argument("--run-admin-crud", action="store_true", help="是否对临时用户执行管理员 CRUD，并最终软删除临时用户；会自动启用注册临时用户")

    parser.add_argument("--run-on-demand-prediction", action="store_true", help="是否额外测试一个 on-demand 预测 ticker")
    parser.add_argument("--on-demand-ticker", default="META", help="on-demand 预测测试 ticker")
    parser.add_argument("--strict-prediction-checks", action="store_true", help="on-demand 预测也严格要求 200 和结构校验")

    parser.add_argument("--run-intraday-check", action="store_true", help="是否测试 range=1d 小时级行情；该检查依赖 AKShare，默认关闭")
    parser.add_argument("--run-news-force-fetch-check", action="store_true", help="是否强制抓取一条新闻原文；可能受反爬影响，默认关闭")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    forecast_days = max(1, min(int(args.forecast_days), MAX_FORECAST_DAYS))
    prediction_base_date = args.prediction_base_date.strip() if isinstance(args.prediction_base_date, str) else args.prediction_base_date
    if prediction_base_date == "":
        prediction_base_date = None

    tester = FinsightApiTester(
        base_url=args.base_url,
        admin_user=args.admin_user,
        admin_pass=args.admin_pass,
        normal_user=args.user,
        normal_pass=args.user_pass,
        output_dir=args.output_dir,
        timeout=args.timeout,
        prediction_ticker=args.prediction_ticker,
        prediction_base_date=prediction_base_date,
        forecast_days=forecast_days,
        news_window_days=args.news_window_days,
        pipeline_ticker=args.pipeline_ticker,
        pipeline_target_date=args.pipeline_target_date,
        pipeline_modules=[x.strip() for x in args.pipeline_modules.split(",") if x.strip()],
        run_data_pipeline=args.run_data_pipeline,
        run_daily_refresh=args.run_daily_refresh,
        daily_refresh_target_date=args.daily_refresh_target_date,
        run_stock_universe_sync=args.run_stock_universe_sync,
        run_auth_register=args.run_auth_register or args.run_admin_crud,
        run_admin_crud=args.run_admin_crud,
        run_on_demand_prediction=args.run_on_demand_prediction,
        on_demand_ticker=args.on_demand_ticker,
        run_intraday_check=args.run_intraday_check,
        run_news_force_fetch_check=args.run_news_force_fetch_check,
        strict_prediction_checks=args.strict_prediction_checks,
    )
    tester.run_all()


if __name__ == "__main__":
    main()

