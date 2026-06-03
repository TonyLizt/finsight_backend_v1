#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Finsight API 自动化测试脚本

适用版本：
    Finsight Backend v1.2 Integrated

作用：
1. 自动请求后端主要 API。
2. 自动登录普通用户和管理员，自动携带 JWT Token。
3. 记录每次请求的方法、URL、请求参数、请求体、状态码、返回值和耗时。
4. 输出 JSON 与 Markdown 两份测试报告。
5. 对 v1.2 预测结果进行基础业务校验：
   - 分类概率字段存在；
   - prob_down + prob_neutral + prob_up 约等于 1；
   - price_path 长度符合 forecast_days；
   - model_version / reg_model_version 存在；
   - data_refresh_status 尽量记录。

默认后端地址：
    http://127.0.0.1:8002

运行示例：
    python finsight_api_auto_test.py
    python finsight_api_auto_test.py --base-url http://127.0.0.1:8002
    python finsight_api_auto_test.py --prediction-base-date 2026-06-02
    python finsight_api_auto_test.py --run-daily-refresh --daily-refresh-target-date 2026-06-02

依赖：
    pip install requests
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


SENSITIVE_KEYS = {
    "password",
    "confirm_password",
    "new_password",
    "token",
    "authorization",
    "Authorization",
}


def now_str() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_json(obj: Any) -> Any:
    try:
        json.dumps(obj, ensure_ascii=False)
        return obj
    except TypeError:
        return str(obj)


def mask_sensitive(obj: Any) -> Any:
    if isinstance(obj, dict):
        result = {}
        for key, value in obj.items():
            if key in SENSITIVE_KEYS or str(key).lower() in {x.lower() for x in SENSITIVE_KEYS}:
                result[key] = "***MASKED***"
            else:
                result[key] = mask_sensitive(value)
        return result
    if isinstance(obj, list):
        return [mask_sensitive(x) for x in obj]
    return obj


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
    expected_status: List[int]
    request_headers: Dict[str, Any] = field(default_factory=dict)
    request_params: Optional[Dict[str, Any]] = None
    request_json: Optional[Dict[str, Any]] = None
    status_code: Optional[int] = None
    elapsed_ms: Optional[float] = None
    response: Any = None
    ok: bool = False
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


class FinsightApiTester:
    def __init__(
        self,
        base_url: str,
        admin_user: str,
        admin_pass: str,
        normal_user: str,
        normal_pass: str,
        output_dir: str,
        timeout: int = 20,
        ticker: str = "AAPL",
        prediction_base_date: Optional[str] = None,
        run_daily_refresh: bool = False,
        daily_refresh_target_date: Optional[str] = None,
        daily_refresh_force: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.admin_user = admin_user
        self.admin_pass = admin_pass
        self.normal_user = normal_user
        self.normal_pass = normal_pass
        self.timeout = timeout
        self.ticker = ticker.upper()
        self.prediction_base_date = prediction_base_date
        self.run_daily_refresh = run_daily_refresh
        self.daily_refresh_target_date = daily_refresh_target_date
        self.daily_refresh_force = daily_refresh_force

        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.records: List[TestRecord] = []
        self.admin_token: Optional[str] = None
        self.user_token: Optional[str] = None

        self.temp_username = f"api_test_{int(time.time())}"
        self.temp_password = "TestUser123"
        self.temp_user_id: Optional[int] = None
        self.created_prediction_id: Optional[int] = None
        self.created_run_id: Optional[int] = None

    def request(
        self,
        name: str,
        method: str,
        path: str,
        *,
        token: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        expected_status: Optional[List[int]] = None,
    ) -> Tuple[Optional[requests.Response], TestRecord]:
        expected_status = expected_status or [200]
        url = f"{self.base_url}{path}"

        headers: Dict[str, str] = {"Content-Type": "application/json"}
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

        start = time.time()
        response: Optional[requests.Response] = None

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

        except Exception as exc:
            record.elapsed_ms = round((time.time() - start) * 1000, 2)
            record.error = repr(exc)
            record.ok = False

        self.records.append(record)
        status = record.status_code if record.status_code is not None else "ERR"
        print(f"[{'PASS' if record.ok else 'FAIL'}] {record.name} -> {status} ({record.elapsed_ms} ms)")
        return response, record

    @staticmethod
    def get_body(resp: Optional[requests.Response]) -> Dict[str, Any]:
        if resp is None:
            return {}
        try:
            body = resp.json()
            return body if isinstance(body, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def get_data(resp: Optional[requests.Response]) -> Dict[str, Any]:
        body = FinsightApiTester.get_body(resp)
        data = body.get("data")
        return data if isinstance(data, dict) else {}

    @staticmethod
    def get_items(resp: Optional[requests.Response]) -> List[Dict[str, Any]]:
        data = FinsightApiTester.get_data(resp)
        items = data.get("items")
        return items if isinstance(items, list) else []

    def add_warning(self, record: TestRecord, message: str) -> None:
        record.warnings.append(message)
        print(f"  [WARN] {message}")

    def run_all(self) -> None:
        self.test_health()
        self.test_auth()
        self.test_stock_api()
        self.test_watchlist_api()
        self.test_prediction_api()
        self.test_backtest_api()
        self.test_model_api()
        self.test_crawler_api()
        self.test_log_api()
        self.test_admin_user_api()
        self.write_reports()

    def test_health(self) -> None:
        self.request("Health Check", "GET", "/health", expected_status=[200])

    def test_auth(self) -> None:
        resp, _ = self.request(
            "Auth - Register Temp User",
            "POST",
            "/api/auth/register",
            json_body={
                "username": self.temp_username,
                "password": self.temp_password,
                "confirm_password": self.temp_password,
            },
            expected_status=[200, 400, 409],
        )
        self.temp_user_id = self.get_data(resp).get("user_id") or self.get_data(resp).get("id")

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

        self.request("Auth - Get Me Normal User", "GET", "/api/auth/me", token=self.user_token, expected_status=[200])
        self.request("Auth - Get Me Admin", "GET", "/api/auth/me", token=self.admin_token, expected_status=[200])

    def test_stock_api(self) -> None:
        resp, _ = self.request(
            f"Stock - Search {self.ticker}",
            "GET",
            "/api/stocks/search",
            token=self.user_token,
            params={"keyword": self.ticker, "only_supported": False, "include_etf": True, "limit": 10},
            expected_status=[200],
        )

        items = self.get_items(resp)
        if items:
            self.ticker = (items[0].get("ticker") or self.ticker).upper()

        self.request(
            "Stock - Detail",
            "GET",
            f"/api/stocks/{self.ticker}/detail",
            token=self.user_token,
            params={
                "range": "1m",
                "include_news": True,
                "include_indicators": True,
                "auto_refresh": False,
            },
            expected_status=[200, 404],
        )

        news_resp, _ = self.request(
            "Stock - News List",
            "GET",
            f"/api/stocks/{self.ticker}/news",
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
        else:
            self.request(
                "Stock - News Detail Not Found",
                "GET",
                "/api/stocks/news/999999",
                token=self.user_token,
                params={"include_html": False},
                expected_status=[404, 400, 200],
            )

        self.request(
            "Stock - Sentiment Summary",
            "GET",
            f"/api/stocks/{self.ticker}/sentiment-summary",
            token=self.user_token,
            params={"window_days": 7},
            expected_status=[200, 404],
        )

    def test_watchlist_api(self) -> None:
        self.request(
            f"Watchlist - Add {self.ticker}",
            "POST",
            "/api/watchlist",
            token=self.user_token,
            json_body={"ticker": self.ticker, "auto_fetch": True},
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
            f"Watchlist - Delete {self.ticker}",
            "DELETE",
            f"/api/watchlist/{self.ticker}",
            token=self.user_token,
            expected_status=[200, 404],
        )

    def validate_prediction_response(self, data: Dict[str, Any], record: TestRecord, expected_days: int) -> None:
        if not data:
            self.add_warning(record, "Prediction response has no data. This can happen when online market fetch fails or no cached feature snapshot exists.")
            return

        for key in ["prediction_id", "ticker", "base_trading_date", "classification", "regression", "model_version"]:
            if key not in data:
                self.add_warning(record, f"Prediction data missing key: {key}")

        classification = data.get("classification") or {}
        prob_down = classification.get("prob_down")
        prob_neutral = classification.get("prob_neutral")
        prob_up = classification.get("prob_up")

        if all(isinstance(x, (int, float)) for x in [prob_down, prob_neutral, prob_up]):
            total = float(prob_down) + float(prob_neutral) + float(prob_up)
            if abs(total - 1.0) > 0.05:
                self.add_warning(record, f"Classification probabilities sum to {total}, expected about 1.0")
        else:
            self.add_warning(record, "Classification probabilities are incomplete.")

        regression = data.get("regression") or {}
        price_path = regression.get("price_path") or []
        if isinstance(price_path, list):
            if len(price_path) != expected_days:
                self.add_warning(record, f"price_path length={len(price_path)}, expected={expected_days}")
        else:
            self.add_warning(record, "regression.price_path is not a list.")

        refresh = data.get("data_refresh_status")
        if refresh is None:
            # Older prediction_service may store data_refresh_status only in explanation_json,
            # so this is a warning rather than a hard failure.
            self.add_warning(record, "data_refresh_status missing in prediction response.")

    def test_prediction_api(self) -> None:
        forecast_days = 5
        request_body: Dict[str, Any] = {
            "ticker": self.ticker,
            "forecast_days": forecast_days,
            "analysis_mode": "full",
            "risk_profile": "balanced",
            "news_window_days": 7,
            "force_refresh": False,
        }

        if self.prediction_base_date:
            request_body["base_trading_date"] = self.prediction_base_date

        resp, record = self.request(
            f"Prediction - Run {self.ticker}",
            "POST",
            "/api/predictions/run",
            token=self.user_token,
            json_body=request_body,
            expected_status=[200, 400, 404],
        )

        body = self.get_body(resp)
        data = self.get_data(resp)

        if resp is not None and resp.status_code == 200:
            self.validate_prediction_response(data, record, forecast_days)
            self.created_prediction_id = data.get("prediction_id") or data.get("id")
        else:
            # Online market data may fail due to API quota / 403. Record response but do not hide it.
            err_code = body.get("error_code")
            msg = body.get("message")
            self.add_warning(record, f"Prediction did not return 200. error_code={err_code}, message={msg}")

        hist_resp, _ = self.request(
            "Prediction - History",
            "GET",
            "/api/predictions/history",
            token=self.user_token,
            params={"ticker": self.ticker, "page": 1, "page_size": 20},
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
            )
        else:
            self.request(
                "Prediction - Detail Not Found",
                "GET",
                "/api/predictions/999999",
                token=self.user_token,
                expected_status=[404, 400],
            )

    def test_backtest_api(self) -> None:
        resp, _ = self.request(
            "Backtest - Run",
            "POST",
            "/api/backtest/run",
            token=self.user_token,
            json_body={
                "run_name": "API Auto Test Backtest",
                "tickers": [self.ticker, "MSFT"],
                "start_date": "2024-01-01",
                "end_date": "2024-01-31",
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
            expected_status=[200, 400, 404],
        )
        data = self.get_data(resp)
        self.created_run_id = data.get("run_id") or data.get("id") or 1

        run_id = self.created_run_id
        self.request("Backtest - Status", "GET", f"/api/backtest/{run_id}/status", token=self.user_token, expected_status=[200, 404])
        self.request(
            "Backtest - Frames",
            "GET",
            f"/api/backtest/{run_id}/frames",
            token=self.user_token,
            params={"limit": 3, "include_positions": True, "include_position_curves": True},
            expected_status=[200, 404],
        )
        self.request(
            "Backtest - Logs",
            "GET",
            f"/api/backtest/{run_id}/logs",
            token=self.user_token,
            params={"after_log_id": 0, "limit": 20},
            expected_status=[200, 404],
        )
        self.request("Backtest - Day Detail", "GET", f"/api/backtest/{run_id}/days/2024-01-02", token=self.user_token, expected_status=[200, 404, 400])
        self.request("Backtest - Summary", "GET", f"/api/backtest/{run_id}/summary", token=self.user_token, expected_status=[200, 404])
        self.request("Backtest - Final Positions By Run", "GET", f"/api/backtest/{run_id}/final-positions", token=self.user_token, expected_status=[200, 404])
        self.request(
            "Backtest - Latest Final Positions",
            "GET",
            "/api/backtest/latest/final-positions",
            token=self.user_token,
            params={"include_empty": True},
            expected_status=[200, 404],
        )

    def test_model_api(self) -> None:
        resp, record = self.request(
            "Model - Active Models",
            "GET",
            "/api/models/active",
            token=self.user_token,
            expected_status=[200, 404],
        )

        data = self.get_data(resp)
        if resp is not None and resp.status_code == 200:
            text_data = pretty(data)
            if "finsight_cls_abs_h15_v1.2" not in text_data:
                self.add_warning(record, "Active classifier v1.2 not found in /api/models/active response.")
            if "finsight_reg_return_path_v1.2" not in text_data:
                self.add_warning(record, "Active regressor v1.2 not found in /api/models/active response.")

    def test_crawler_api(self) -> None:
        self.request("Crawler - Status", "GET", "/api/crawler/status", token=self.admin_token, expected_status=[200, 403, 404])
        self.request("Crawler - Stock Universe Status", "GET", "/api/crawler/stock-universe/status", token=self.admin_token, expected_status=[200, 403, 404])
        self.request(
            "Crawler - Trigger Stock Universe Sync",
            "POST",
            "/api/crawler/stock-universe/sync",
            token=self.admin_token,
            json_body={"force": False},
            expected_status=[200, 403, 404],
        )

        self.request(
            "Crawler - Daily Refresh Status",
            "GET",
            "/api/crawler/daily-refresh/status",
            token=self.admin_token,
            expected_status=[200, 403, 404],
        )

        if self.run_daily_refresh:
            body: Dict[str, Any] = {
                "tickers": [self.ticker],
                "force_refresh": self.daily_refresh_force,
                "limit": 10,
            }
            if self.daily_refresh_target_date:
                body["target_date"] = self.daily_refresh_target_date

            self.request(
                "Crawler - Trigger Daily Refresh",
                "POST",
                "/api/crawler/daily-refresh/run",
                token=self.admin_token,
                json_body=body,
                expected_status=[200, 400, 403, 404],
            )

    def test_log_api(self) -> None:
        self.request(
            "Log - Query Logs",
            "GET",
            "/api/logs",
            token=self.admin_token,
            params={"page": 1, "page_size": 20},
            expected_status=[200, 403],
        )

    def test_admin_user_api(self) -> None:
        resp, _ = self.request(
            "Admin - User List",
            "GET",
            "/api/admin/users",
            token=self.admin_token,
            params={"page": 1, "page_size": 20},
            expected_status=[200, 403],
        )

        if not self.temp_user_id:
            for item in self.get_items(resp):
                if item.get("username") == self.temp_username:
                    self.temp_user_id = item.get("user_id") or item.get("id")
                    break

        target_user_id = self.temp_user_id

        if target_user_id:
            self.request("Admin - User Detail Temp", "GET", f"/api/admin/users/{target_user_id}", token=self.admin_token, expected_status=[200, 404, 403])
            self.request(
                "Admin - Update Temp User Status",
                "PUT",
                f"/api/admin/users/{target_user_id}/status",
                token=self.admin_token,
                json_body={"status": "active", "reason": "API 自动化测试"},
                expected_status=[200, 400, 403, 404],
            )
            self.request(
                "Admin - Update Temp User Role",
                "PUT",
                f"/api/admin/users/{target_user_id}/role",
                token=self.admin_token,
                json_body={"role": "user", "reason": "API 自动化测试"},
                expected_status=[200, 400, 403, 404],
            )
            new_name = f"{self.temp_username}_renamed"
            self.request(
                "Admin - Update Temp Username",
                "PUT",
                f"/api/admin/users/{target_user_id}/username",
                token=self.admin_token,
                json_body={"username": new_name, "reason": "API 自动化测试修改用户名"},
                expected_status=[200, 400, 403, 404, 409],
            )
            self.request(
                "Admin - Reset Temp User Password",
                "PUT",
                f"/api/admin/users/{target_user_id}/password",
                token=self.admin_token,
                json_body={
                    "new_password": "NewTemp123",
                    "confirm_password": "NewTemp123",
                    "force_logout": True,
                    "reason": "API 自动化测试重置密码",
                },
                expected_status=[200, 400, 403, 404],
            )
            self.request(
                "Admin - Delete Temp User Soft",
                "DELETE",
                f"/api/admin/users/{target_user_id}",
                token=self.admin_token,
                params={"hard_delete": False},
                json_body={"reason": "API 自动化测试软删除临时用户"},
                expected_status=[200, 400, 403, 404],
            )
        else:
            self.request(
                "Admin - User Detail Not Found",
                "GET",
                "/api/admin/users/999999",
                token=self.admin_token,
                expected_status=[404, 400, 403],
            )

    def write_reports(self) -> None:
        ts = now_str()
        json_path = self.output_dir / f"finsight_api_test_report_{ts}.json"
        md_path = self.output_dir / f"finsight_api_test_report_{ts}.md"

        records_as_dicts = [
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
                "warnings": r.warnings,
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
            "warnings": sum(len(r.warnings) for r in self.records),
        }

        json_path.write_text(
            json.dumps({"summary": summary, "records": records_as_dicts}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        lines: List[str] = []
        lines.append("# Finsight API 自动化测试报告")
        lines.append("")
        lines.append(f"- Base URL: `{self.base_url}`")
        lines.append(f"- Generated At: `{summary['generated_at']}`")
        lines.append(f"- Total: **{summary['total']}**")
        lines.append(f"- Passed: **{summary['passed']}**")
        lines.append(f"- Failed: **{summary['failed']}**")
        lines.append(f"- Warnings: **{summary['warnings']}**")
        lines.append("")
        lines.append("## 汇总表")
        lines.append("")
        lines.append("| # | 结果 | 接口 | 方法 | 状态码 | 耗时 ms | Warnings |")
        lines.append("|---:|---|---|---|---:|---:|---:|")
        for idx, r in enumerate(self.records, 1):
            result = "✅ PASS" if r.ok else "❌ FAIL"
            lines.append(f"| {idx} | {result} | {r.name} | {r.method} | {r.status_code} | {r.elapsed_ms} | {len(r.warnings)} |")
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
            if r.warnings:
                lines.append("- Warnings:")
                for warning in r.warnings:
                    lines.append(f"  - {warning}")
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Finsight API 自动化测试脚本")
    parser.add_argument("--base-url", default="http://127.0.0.1:8002", help="后端服务地址")
    parser.add_argument("--admin-user", default="admin", help="管理员用户名")
    parser.add_argument("--admin-pass", default="Admin123", help="管理员密码")
    parser.add_argument("--user", default="user01", help="普通用户用户名")
    parser.add_argument("--user-pass", default="User123", help="普通用户密码")
    parser.add_argument("--output-dir", default="api_test_results", help="测试报告输出目录")
    parser.add_argument("--timeout", type=int, default=20, help="单个请求超时时间，秒")
    parser.add_argument("--ticker", default="AAPL", help="测试股票代码")
    parser.add_argument("--prediction-base-date", default=None, help="预测基准日，例如 2026-06-02")
    parser.add_argument("--run-daily-refresh", action="store_true", help="是否触发每日数据补全接口")
    parser.add_argument("--daily-refresh-target-date", default=None, help="每日补全目标日期，例如 2026-06-02")
    parser.add_argument("--daily-refresh-force", action="store_true", help="每日补全是否强制访问外部行情源")
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
        ticker=args.ticker,
        prediction_base_date=args.prediction_base_date,
        run_daily_refresh=args.run_daily_refresh,
        daily_refresh_target_date=args.daily_refresh_target_date,
        daily_refresh_force=args.daily_refresh_force,
    )
    tester.run_all()


if __name__ == "__main__":
    main()
