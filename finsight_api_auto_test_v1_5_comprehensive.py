#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Finsight API 自动化测试脚本 v1.5-comprehensive

基于原 v1.3/v1.4 测试脚本增强，适配当前后端：
- Auth API v2：password_sha256，而不是明文 password。
- BacktestRunRequest：只传后端允许的字段，避免 extra=forbid 422。
- 管理员重置密码：new_password_sha256 / confirm_password_sha256。
- 默认覆盖绝大部分核心接口；默认跳过最耗时/外部依赖最重的接口。

默认会测试：
health、auth、stocks、watchlist、models、data-pipeline coverage、prediction、backtest 壳与轮询接口、crawler 状态、logs、admin users。

默认跳过的重接口：
- POST /api/data-pipeline/jobs
- POST /api/crawler/daily-refresh/run
- POST /api/crawler/stock-universe/sync
- 等待 backtest 完整跑完

依赖：pip install requests
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import requests


PASSWORD_PREFIX = "FINSIGHT_CLIENT_PASSWORD_V1:"
NATIVE_MODEL_FORECAST_DAYS = 5

SENSITIVE_KEYS = {
    "password",
    "confirm_password",
    "new_password",
    "password_sha256",
    "confirm_password_sha256",
    "new_password_sha256",
    "token",
    "authorization",
    "Authorization",
    "ALPHA_VANTAGE_API_KEY",
    "DASHSCOPE_API_KEY",
    "TWELVEDATA_API_KEY",
}


def password_to_sha256_hex(raw_password: str) -> str:
    material = f"{PASSWORD_PREFIX}{raw_password or ''}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def now_str() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def mask_sensitive(obj: Any) -> Any:
    if isinstance(obj, dict):
        sensitive_lower = {x.lower() for x in SENSITIVE_KEYS}
        return {
            k: "***MASKED***" if str(k).lower() in sensitive_lower else mask_sensitive(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [mask_sensitive(x) for x in obj]
    return obj


def safe_json(obj: Any) -> Any:
    try:
        json.dumps(obj, ensure_ascii=False)
        return obj
    except Exception:
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
    for f in fields:
        if f not in obj:
            errors.append(f"{prefix} missing {f}")


@dataclass
class TestRecord:
    name: str
    method: str
    url: str
    expected_status: list[int]
    profile: str = "-"
    request_headers: dict[str, Any] = field(default_factory=dict)
    request_params: Optional[dict[str, Any]] = None
    request_json: Optional[dict[str, Any]] = None
    status_code: Optional[int] = None
    elapsed_ms: Optional[float] = None
    response: Any = None
    ok: bool = False
    skipped: bool = False
    error: Optional[str] = None
    validation_errors: list[str] = field(default_factory=list)


class FinsightApiTester:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.base_url = args.base_url.rstrip("/")
        self.output_dir = Path(args.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = args.timeout

        self.admin_token: Optional[str] = None
        self.user_token: Optional[str] = None
        self.admin_user_id: Optional[int] = None
        self.normal_user_id: Optional[int] = None
        self.normal_user_effective_token: Optional[str] = None

        self.created_prediction_id: Optional[int] = None
        self.created_backtest_run_id: Optional[int] = None
        self.created_backtest_start_date: Optional[str] = None
        self.first_news_id: Optional[int] = None
        self.temp_user_id: Optional[int] = None
        self.temp_username: Optional[str] = None
        self.temp_password = "TempUser123"
        self.records: list[TestRecord] = []

    # -------------------------
    # Request / reporting helpers
    # -------------------------

    def request(
        self,
        name: str,
        method: str,
        path: str,
        *,
        token: Optional[str] = None,
        profile: str = "-",
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
        expected_status: Optional[list[int]] = None,
        validator: Optional[Callable[[Any], list[str]]] = None,
        validate_non_2xx: bool = False,
    ) -> tuple[Optional[requests.Response], TestRecord]:
        expected_status = expected_status or [200]
        url = f"{self.base_url}{path}"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        rec = TestRecord(
            name=name,
            method=method.upper(),
            url=url,
            expected_status=expected_status,
            profile=profile,
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
            rec.elapsed_ms = round((time.time() - start) * 1000, 2)
            rec.status_code = response.status_code
            try:
                rec.response = response.json()
            except Exception:
                rec.response = response.text[:5000]

            rec.ok = response.status_code in expected_status
            should_validate = validator is not None and rec.ok and (validate_non_2xx or 200 <= response.status_code < 300)
            if should_validate:
                rec.validation_errors = validator(rec.response)
                if rec.validation_errors:
                    rec.ok = False
        except Exception as exc:
            rec.elapsed_ms = round((time.time() - start) * 1000, 2)
            rec.error = repr(exc)
            rec.ok = False

        self.records.append(rec)
        status = rec.status_code if rec.status_code is not None else "ERR"
        suffix = f" validation_errors={len(rec.validation_errors)}" if rec.validation_errors else ""
        print(f"[{'PASS' if rec.ok else 'FAIL'}] {name} -> {status} ({rec.elapsed_ms} ms){suffix}")
        return response, rec

    def skip(self, name: str, reason: str) -> None:
        rec = TestRecord(name=name, method="-", url="-", expected_status=[], skipped=True, ok=True, response={"skip_reason": reason})
        self.records.append(rec)
        print(f"[SKIP] {name} -> {reason}")

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

    def user_token_or_admin_fallback(self) -> Optional[str]:
        if self.user_token:
            return self.user_token
        return self.admin_token

    # -------------------------
    # Test groups
    # -------------------------

    def run_all(self) -> None:
        self.test_health()
        self.test_auth()
        self.test_stock_api()
        self.test_watchlist_api()
        self.test_model_api()
        self.test_data_pipeline_api()
        if not self.args.skip_prediction:
            self.test_prediction_api()
        else:
            self.skip("Prediction API", "--skip-prediction enabled")
        if not self.args.skip_backtest:
            self.test_backtest_api()
        else:
            self.skip("Backtest API", "--skip-backtest enabled")
        self.test_crawler_api()
        self.test_log_api()
        self.test_admin_user_api()
        self.write_reports()

    def test_health(self) -> None:
        self.request("Health Check", "GET", "/health", expected_status=[200], validator=self.validate_success_data)

    def test_auth(self) -> None:
        # Optional compatibility check: old plaintext password payload should now fail with 422.
        if self.args.check_old_password_payload:
            self.request(
                "Auth - Old Plain Password Payload Should Fail",
                "POST",
                "/api/auth/login",
                json_body={"username": self.args.admin_user, "password": self.args.admin_pass},
                expected_status=[422],
                validate_non_2xx=False,
            )

        normal_login, normal_rec = self.request(
            "Auth - Login Normal User",
            "POST",
            "/api/auth/login",
            profile="user",
            json_body={"username": self.args.user, "password_sha256": password_to_sha256_hex(self.args.user_pass)},
            expected_status=[200],
            validator=self.validate_login_response,
        )
        if normal_rec.ok:
            data = self.get_data(normal_login)
            self.user_token = data.get("token")
            self.normal_user_id = data.get("user_id")

        admin_login, admin_rec = self.request(
            "Auth - Login Admin",
            "POST",
            "/api/auth/login",
            profile="admin",
            json_body={"username": self.args.admin_user, "password_sha256": password_to_sha256_hex(self.args.admin_pass)},
            expected_status=[200],
            validator=self.validate_login_response,
        )
        if admin_rec.ok:
            data = self.get_data(admin_login)
            self.admin_token = data.get("token")
            self.admin_user_id = data.get("user_id")

        if self.user_token:
            self.request("Auth - Me Normal", "GET", "/api/auth/me", token=self.user_token, profile="user", expected_status=[200], validator=self.validate_me_response)
        else:
            self.skip("Auth - Me Normal", "normal user login failed; later user APIs will use admin token fallback")

        if self.admin_token:
            self.request("Auth - Me Admin", "GET", "/api/auth/me", token=self.admin_token, profile="admin", expected_status=[200], validator=self.validate_me_response)
        else:
            self.skip("Auth - Me Admin", "admin login failed")

        if self.args.include_admin_crud and self.admin_token:
            self.register_temp_user_for_admin_crud()

    def register_temp_user_for_admin_crud(self) -> None:
        username = f"api_test_{utc_compact()}"
        digest = password_to_sha256_hex(self.temp_password)
        resp, rec = self.request(
            "Auth - Register Temp User",
            "POST",
            "/api/auth/register",
            json_body={"username": username, "password_sha256": digest, "confirm_password_sha256": digest},
            expected_status=[200, 400],
            validator=self.validate_register_response,
        )
        if rec.ok and rec.status_code == 200:
            data = self.get_data(resp)
            self.temp_user_id = data.get("user_id")
            self.temp_username = data.get("username") or username
            login_resp, login_rec = self.request(
                "Auth - Login Temp User",
                "POST",
                "/api/auth/login",
                profile="temp_user",
                json_body={"username": self.temp_username, "password_sha256": digest},
                expected_status=[200],
                validator=self.validate_login_response,
            )
            if login_rec.ok:
                temp_token = self.get_data(login_resp).get("token")
                self.request("Auth - Me Temp User", "GET", "/api/auth/me", token=temp_token, profile="temp_user", expected_status=[200], validator=self.validate_me_response)

    def test_stock_api(self) -> None:
        token = self.user_token_or_admin_fallback()
        if not token:
            self.skip("Stock API", "no token")
            return
        ticker = self.args.prediction_ticker.upper()

        resp, _ = self.request(
            f"Stock - Search {ticker}",
            "GET",
            "/api/stocks/search",
            token=token,
            profile="user_api",
            params={"keyword": ticker, "only_supported": False, "include_etf": True, "limit": 10},
            expected_status=[200],
            validator=self.validate_stock_search,
        )
        items = self.get_items(resp)
        if items:
            ticker = items[0].get("ticker") or ticker

        self.request(
            "Stock - Detail 3m",
            "GET",
            f"/api/stocks/{ticker}/detail",
            token=token,
            profile="user_api",
            params={"range": "3m", "include_news": True, "include_indicators": True, "auto_refresh": False},
            expected_status=[200, 404],
            validator=self.validate_stock_detail,
        )

        if not self.args.skip_intraday:
            self.request(
                "Stock - Detail 1d Intraday",
                "GET",
                f"/api/stocks/{ticker}/detail",
                token=token,
                profile="user_api",
                params={"range": "1d", "interval": "1min", "include_news": False, "include_indicators": False, "auto_refresh": False},
                expected_status=[200, 404, 500],
                validator=self.validate_stock_detail_light,
            )
        else:
            self.skip("Stock - Detail 1d Intraday", "--skip-intraday enabled")

        news_resp, _ = self.request(
            "Stock - News List",
            "GET",
            f"/api/stocks/{ticker}/news",
            token=token,
            profile="user_api",
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
                    token=token,
                    profile="user_api",
                    params={"include_html": False},
                    expected_status=[200, 404, 500],
                    validator=self.validate_news_detail,
                )
        else:
            self.skip("Stock - News Detail", "news list empty")

        self.request(
            "Stock - Sentiment Summary 14d",
            "GET",
            f"/api/stocks/{ticker}/sentiment-summary",
            token=token,
            profile="user_api",
            params={"window_days": 14},
            expected_status=[200, 404],
            validator=self.validate_sentiment_summary,
        )

        self.request(
            "Stock - News List Positive Filter",
            "GET",
            f"/api/stocks/{ticker}/news",
            token=token,
            profile="user_api",
            params={"limit": 3, "sentiment_label": "positive"},
            expected_status=[200, 404],
            validator=self.validate_news_list,
        )

    def test_watchlist_api(self) -> None:
        token = self.user_token_or_admin_fallback()
        if not token:
            self.skip("Watchlist API", "no token")
            return
        ticker = self.args.prediction_ticker.upper()
        self.request(
            f"Watchlist - Add {ticker}",
            "POST",
            "/api/watchlist",
            token=token,
            profile="user_api",
            json_body={"ticker": ticker, "auto_fetch": False},
            expected_status=[200, 400, 404, 409],
            validator=self.validate_watchlist_add,
        )
        self.request(
            "Watchlist - List With Curve",
            "GET",
            "/api/watchlist",
            token=token,
            profile="user_api",
            params={"include_curve": True},
            expected_status=[200],
            validator=self.validate_watchlist_list,
        )
        self.request(
            "Watchlist - List Without Curve",
            "GET",
            "/api/watchlist",
            token=token,
            profile="user_api",
            params={"include_curve": False},
            expected_status=[200],
            validator=self.validate_watchlist_list,
        )
        self.request(
            f"Watchlist - Delete {ticker}",
            "DELETE",
            f"/api/watchlist/{ticker}",
            token=token,
            profile="user_api",
            expected_status=[200, 404],
            validator=self.validate_watchlist_delete,
        )

    def test_model_api(self) -> None:
        token = self.user_token_or_admin_fallback()
        if not token:
            self.skip("Model API", "no token")
            return
        self.request("Model - Active Models", "GET", "/api/models/active", token=token, profile="user_api", expected_status=[200, 404, 500], validator=self.validate_active_models)

    def test_data_pipeline_api(self) -> None:
        self.request(
            "Data Pipeline - Coverage",
            "GET",
            "/api/data-pipeline/coverage",
            params={"ticker": self.args.pipeline_ticker.upper(), "end_date": self.args.pipeline_target_date or None},
            expected_status=[200],
            validator=self.validate_data_pipeline_coverage,
        )
        if self.args.include_heavy or self.args.include_data_pipeline_job:
            self.request(
                "Data Pipeline - Run Job (Heavy)",
                "POST",
                "/api/data-pipeline/jobs",
                json_body={
                    "tickers": [self.args.pipeline_ticker.upper()],
                    "start_date": None,
                    "end_date": self.args.pipeline_target_date or None,
                    "modules": [x.strip() for x in self.args.pipeline_modules.split(",") if x.strip()],
                    "force_refresh": False,
                    "run_async": False,
                },
                expected_status=[200, 400, 500],
                validator=self.validate_data_pipeline_job,
            )
        else:
            self.skip("Data Pipeline - Run Job", "heavy endpoint skipped by default")

    def test_prediction_api(self) -> None:
        token = self.user_token_or_admin_fallback()
        if not token:
            self.skip("Prediction API", "no token")
            return
        body: dict[str, Any] = {
            "ticker": self.args.prediction_ticker.upper(),
            "forecast_days": self.args.forecast_days,
            "analysis_mode": "full",
            "risk_profile": "balanced",
            "news_window_days": self.args.news_window_days,
            "force_refresh": False,
        }
        if self.args.prediction_base_date:
            body["base_trading_date"] = self.args.prediction_base_date

        resp, _ = self.request(
            f"Prediction - Run {self.args.prediction_ticker.upper()}",
            "POST",
            "/api/predictions/run",
            token=token,
            profile="user_api",
            json_body=body,
            expected_status=[200, 404, 500],
            validator=self.validate_prediction_response,
        )
        data = self.get_data(resp)
        self.created_prediction_id = data.get("prediction_id") or data.get("id")

        hist_resp, _ = self.request(
            "Prediction - History",
            "GET",
            "/api/predictions/history",
            token=token,
            profile="user_api",
            params={"ticker": self.args.prediction_ticker.upper(), "page": 1, "page_size": 20},
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
                token=token,
                profile="user_api",
                expected_status=[200, 404],
                validator=self.validate_prediction_response,
            )
        else:
            self.skip("Prediction - Detail", "no prediction_id available")

        self.request("Prediction - Detail Not Found", "GET", "/api/predictions/999999999", token=token, profile="user_api", expected_status=[404])

    def test_backtest_api(self) -> None:
        token = self.user_token_or_admin_fallback()
        if not token:
            self.skip("Backtest API", "no token")
            return
        tickers = [x.strip().upper() for x in self.args.backtest_tickers.split(",") if x.strip()]
        if not tickers:
            tickers = [self.args.prediction_ticker.upper()]

        resp, _ = self.request(
            "Backtest - Run Shell",
            "POST",
            "/api/backtest/run",
            token=token,
            profile="user_api",
            json_body={
                "tickers": tickers,
                "start_date": self.args.backtest_start,
                "end_date": self.args.backtest_end,
                "initial_cash": self.args.initial_cash,
                "max_position_ratio": self.args.max_position_ratio,
                "max_holding_count": self.args.max_holding_count,
                "fee_rate": self.args.fee_rate,
                "take_profit_pct": self.args.take_profit_pct,
                "stop_loss_pct": self.args.stop_loss_pct,
            },
            expected_status=[200, 400, 404, 422, 500],
            validator=self.validate_backtest_run,
        )
        data = self.get_data(resp)
        self.created_backtest_run_id = data.get("run_id") or data.get("id")
        self.created_backtest_start_date = data.get("start_date") or self.args.backtest_start

        if not self.created_backtest_run_id:
            self.skip("Backtest Polling APIs", "backtest run_id not available")
            return

        run_id = self.created_backtest_run_id
        self.request("Backtest - Status", "GET", f"/api/backtest/{run_id}/status", token=token, profile="user_api", expected_status=[200, 404], validator=self.validate_backtest_status)
        self.request("Backtest - Frames", "GET", f"/api/backtest/{run_id}/frames", token=token, profile="user_api", params={"limit": 10, "include_positions": True, "include_position_curves": False}, expected_status=[200, 404], validator=self.validate_backtest_frames)
        self.request("Backtest - Logs", "GET", f"/api/backtest/{run_id}/logs", token=token, profile="user_api", params={"limit": 20}, expected_status=[200, 404], validator=self.validate_backtest_logs)
        self.request("Backtest - Day Detail", "GET", f"/api/backtest/{run_id}/days/{self.created_backtest_start_date}", token=token, profile="user_api", expected_status=[200, 404], validator=self.validate_backtest_day_detail)
        self.request("Backtest - Summary", "GET", f"/api/backtest/{run_id}/summary", token=token, profile="user_api", expected_status=[200, 404], validator=self.validate_backtest_summary)
        self.request("Backtest - Final Positions By Run", "GET", f"/api/backtest/{run_id}/final-positions", token=token, profile="user_api", expected_status=[200, 404], validator=self.validate_final_positions)
        self.request("Backtest - Latest Final Positions", "GET", "/api/backtest/latest/final-positions", token=token, profile="user_api", params={"include_empty": True}, expected_status=[200, 404], validator=self.validate_final_positions)

        if self.args.wait_backtest_finish:
            self.wait_backtest_until_terminal(run_id, token)
        else:
            self.skip("Backtest - Wait Until Finished", "heavy wait skipped by default")

    def wait_backtest_until_terminal(self, run_id: int, token: str) -> None:
        deadline = time.time() + self.args.backtest_wait_timeout
        status_value = None
        while time.time() < deadline:
            resp, _ = self.request("Backtest - Poll Status Until Terminal", "GET", f"/api/backtest/{run_id}/status", token=token, profile="user_api", expected_status=[200, 404], validator=self.validate_backtest_status)
            data = self.get_data(resp)
            status_value = data.get("status")
            if status_value in {"finished", "failed"}:
                break
            time.sleep(self.args.backtest_poll_interval)
        if status_value not in {"finished", "failed"}:
            self.skip("Backtest - Terminal Status", f"not terminal within {self.args.backtest_wait_timeout}s; latest status={status_value}")

    def test_crawler_api(self) -> None:
        if not self.admin_token:
            self.skip("Crawler API", "no admin token")
            return
        self.request("Crawler - Status", "GET", "/api/crawler/status", token=self.admin_token, profile="admin", expected_status=[200, 403, 404], validator=self.validate_crawler_status)
        self.request("Crawler - Stock Universe Status", "GET", "/api/crawler/stock-universe/status", token=self.admin_token, profile="admin", expected_status=[200, 403, 404], validator=self.validate_stock_universe_status)
        if self.args.include_heavy or self.args.include_stock_universe_sync:
            self.request("Crawler - Stock Universe Sync (Heavy)", "POST", "/api/crawler/stock-universe/sync", token=self.admin_token, profile="admin", json_body={"force": False}, expected_status=[200, 400, 403, 404, 500], validator=None)
        else:
            self.skip("Crawler - Stock Universe Sync", "heavy endpoint skipped by default")

        self.request("Crawler - Daily Refresh Status", "GET", "/api/crawler/daily-refresh/status", token=self.admin_token, profile="admin", expected_status=[200, 403, 404], validator=self.validate_daily_refresh_status)
        if self.args.include_heavy or self.args.include_daily_refresh_run:
            self.request(
                "Crawler - Daily Refresh Run (Heavy)",
                "POST",
                "/api/crawler/daily-refresh/run",
                token=self.admin_token,
                profile="admin",
                json_body={
                    "tickers": [self.args.prediction_ticker.upper()],
                    "target_date": self.args.daily_refresh_target_date or None,
                    "force_refresh": False,
                    "limit": 1,
                    "modules": ["market", "technical", "features"],
                },
                expected_status=[200, 400, 403, 404, 500],
                validator=None,
            )
        else:
            self.skip("Crawler - Daily Refresh Run", "heavy endpoint skipped by default")

    def test_log_api(self) -> None:
        if not self.admin_token:
            self.skip("Log API", "no admin token")
            return
        self.request("Log - Query Logs", "GET", "/api/logs", token=self.admin_token, profile="admin", params={"page": 1, "page_size": 20}, expected_status=[200, 403], validator=self.validate_logs)
        self.request("Log - Query Prediction Logs", "GET", "/api/logs", token=self.admin_token, profile="admin", params={"module": "PredictionService", "page": 1, "page_size": 5}, expected_status=[200, 403], validator=self.validate_logs)

    def test_admin_user_api(self) -> None:
        if not self.admin_token:
            self.skip("Admin User API", "no admin token")
            return
        list_resp, _ = self.request("Admin - User List", "GET", "/api/admin/users", token=self.admin_token, profile="admin", params={"page": 1, "page_size": 20}, expected_status=[200, 403], validator=self.validate_admin_users_list)
        data = self.get_data(list_resp)
        items = data.get("items") if isinstance(data.get("items"), list) else []
        target_id = self.temp_user_id or (items[0].get("user_id") if items and isinstance(items[0], dict) else None)
        if target_id:
            self.request("Admin - User Detail", "GET", f"/api/admin/users/{target_id}", token=self.admin_token, profile="admin", expected_status=[200, 403, 404], validator=self.validate_admin_user_detail)
        else:
            self.skip("Admin - User Detail", "no user_id available")

        if self.args.include_admin_crud and self.temp_user_id:
            self.test_admin_crud_for_temp_user()
        elif self.args.include_admin_crud:
            self.skip("Admin - Temp User CRUD", "temp user was not created")
        else:
            self.skip("Admin - Temp User CRUD", "not enabled; use --include-admin-crud")

    def test_admin_crud_for_temp_user(self) -> None:
        uid = int(self.temp_user_id)
        new_username = f"{self.temp_username}_renamed"
        self.request("Admin - Update Temp User Status Disabled", "PUT", f"/api/admin/users/{uid}/status", token=self.admin_token, profile="admin", json_body={"status": "disabled", "reason": "api auto test"}, expected_status=[200, 400, 403, 404], validator=self.validate_admin_update_status)
        self.request("Admin - Update Temp User Status Active", "PUT", f"/api/admin/users/{uid}/status", token=self.admin_token, profile="admin", json_body={"status": "active", "reason": "api auto test restore"}, expected_status=[200, 400, 403, 404], validator=self.validate_admin_update_status)
        self.request("Admin - Update Temp User Role User", "PUT", f"/api/admin/users/{uid}/role", token=self.admin_token, profile="admin", json_body={"role": "user", "reason": "api auto test"}, expected_status=[200, 400, 403, 404], validator=self.validate_admin_update_role)
        self.request("Admin - Update Temp Username", "PUT", f"/api/admin/users/{uid}/username", token=self.admin_token, profile="admin", json_body={"username": new_username, "reason": "api auto test"}, expected_status=[200, 400, 403, 404], validator=self.validate_admin_update_username)
        reset_digest = password_to_sha256_hex("TempUser456")
        self.request("Admin - Reset Temp User Password", "PUT", f"/api/admin/users/{uid}/password", token=self.admin_token, profile="admin", json_body={"new_password_sha256": reset_digest, "confirm_password_sha256": reset_digest, "force_logout": True, "reason": "api auto test"}, expected_status=[200, 400, 403, 404], validator=self.validate_admin_reset_password)
        self.request("Admin - Delete Temp User", "DELETE", f"/api/admin/users/{uid}", token=self.admin_token, profile="admin", json_body={"reason": "api auto test cleanup"}, expected_status=[200, 400, 403, 404], validator=self.validate_admin_delete_user)

    # -------------------------
    # Validators
    # -------------------------

    def validate_success_data(self, body: Any) -> list[str]:
        return [] if is_success_body(body) else ["response should be success=true and data object"]

    def validate_login_response(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        add_required(errors, as_data(body), ["token", "user_id", "username", "role", "status"], "login.data")
        return errors

    def validate_register_response(self, body: Any) -> list[str]:
        if not is_success_body(body):
            return []  # 注册用户已存在等 400 在状态码层面处理。
        errors: list[str] = []
        add_required(errors, as_data(body), ["user_id", "username", "role", "status", "created_at"], "register.data")
        return errors

    def validate_me_response(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        add_required(errors, as_data(body), ["user_id", "username", "role", "status"], "me.data")
        return errors

    def validate_stock_search(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        if not isinstance(data.get("items"), list):
            errors.append("stock search items should be a list")
        if "total" not in data:
            errors.append("stock search missing total")
        return errors

    def validate_stock_detail_light(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        add_required(errors, data, ["ticker", "company_name", "market", "is_supported", "raw_is_supported", "data_status", "price_range", "data_frequency", "price_curve_count", "current_quote", "price_curve"], "stock detail")
        if not isinstance(data.get("price_curve"), list):
            errors.append("stock detail price_curve should be list")
        return errors

    def validate_stock_detail(self, body: Any) -> list[str]:
        errors = self.validate_stock_detail_light(body)
        data = as_data(body)
        if not isinstance(data.get("current_quote"), dict):
            errors.append("current_quote should be object")
        else:
            add_required(errors, data["current_quote"], ["current_price", "change_percent", "daily_return", "amplitude", "fifty_two_week_high", "fifty_two_week_low", "volume", "trading_date"], "current_quote")
        if not isinstance(data.get("indicator_curve"), list):
            errors.append("indicator_curve should be list")
        if not isinstance(data.get("latest_news"), list):
            errors.append("latest_news should be list")
        if "sentiment_counts" in data:
            errors.extend(self._validate_sentiment_counts(data.get("sentiment_counts"), "stock detail sentiment_counts"))
        return errors

    def validate_news_list(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        add_required(errors, data, ["ticker", "return_all", "pagination_mode", "cursor", "next_cursor", "has_more", "returned_count", "sentiment_counts", "total", "items"], "news list")
        if not isinstance(data.get("items"), list):
            errors.append("news list items should be list")
        for idx, item in enumerate((data.get("items") or [])[:3]):
            if isinstance(item, dict):
                add_required(errors, item, ["news_id", "title", "summary", "source", "url", "publish_time", "sentiment_score", "sentiment_label", "has_detail"], f"news item {idx}")
        if "sentiment_counts" in data:
            errors.extend(self._validate_sentiment_counts(data.get("sentiment_counts"), "news list sentiment_counts"))
        return errors

    def validate_news_detail(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        add_required(errors, data, ["news_id", "ticker", "title", "summary", "content_text", "source", "url", "publish_time", "sentiment_score", "sentiment_label", "content_status"], "news detail")
        return errors

    def validate_sentiment_summary(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        add_required(errors, data, ["ticker", "sentiment_counts"], "sentiment summary")
        if "sentiment_counts" in data:
            errors.extend(self._validate_sentiment_counts(data.get("sentiment_counts"), "sentiment summary sentiment_counts"))
        return errors

    def validate_watchlist_add(self, body: Any) -> list[str]:
        if not is_success_body(body):
            return []
        errors: list[str] = []
        add_required(errors, as_data(body), ["ticker", "company_name", "is_supported"], "watchlist add")
        return errors

    def validate_watchlist_list(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        if not isinstance(data.get("items"), list):
            errors.append("watchlist items should be list")
        return errors

    def validate_watchlist_delete(self, body: Any) -> list[str]:
        if not is_success_body(body):
            return []
        errors: list[str] = []
        add_required(errors, as_data(body), ["ticker", "deleted"], "watchlist delete")
        return errors

    def validate_active_models(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        add_required(errors, as_data(body), ["classifier", "aux_classifier", "regressor"], "active models")
        return errors

    def validate_data_pipeline_coverage(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        for key in ["price_data", "technical_indicators", "news_data", "sentiment_daily", "model_feature_snapshots", "recommendation"]:
            if key not in data:
                errors.append(f"coverage missing {key}")
        return errors

    def validate_data_pipeline_job(self, body: Any) -> list[str]:
        if not is_success_body(body):
            return []
        errors: list[str] = []
        add_required(errors, as_data(body), ["job_id", "status", "tickers", "modules", "items"], "pipeline job")
        return errors

    def validate_prediction_response(self, body: Any) -> list[str]:
        if not is_success_body(body):
            return []
        errors: list[str] = []
        data = as_data(body)
        add_required(errors, data, ["prediction_id", "ticker", "base_trading_date", "forecast_days", "model_version", "reg_model_version", "request_params", "classification", "regression", "data_refresh_status", "news_summary", "explanations", "llm_report"], "prediction")
        cls = data.get("classification") if isinstance(data.get("classification"), dict) else {}
        add_required(errors, cls, ["predicted_label", "prob_up", "prob_neutral", "prob_down", "predicted_growth_prob"], "classification")
        try:
            prob_sum = float(cls.get("prob_up", 0)) + float(cls.get("prob_neutral", 0)) + float(cls.get("prob_down", 0))
            if not math.isclose(prob_sum, 1.0, rel_tol=1e-3, abs_tol=1e-3):
                errors.append(f"classification probabilities sum to {prob_sum}, not close to 1")
        except Exception:
            errors.append("classification probabilities are not numeric")
        reg = data.get("regression") if isinstance(data.get("regression"), dict) else {}
        path = reg.get("price_path")
        if not isinstance(path, list):
            errors.append("regression.price_path should be list")
        else:
            expected = int(data.get("forecast_days") or self.args.forecast_days)
            if len(path) != expected:
                errors.append(f"price_path length {len(path)} != forecast_days {expected}")
        news = data.get("news_summary")
        if not isinstance(news, dict):
            errors.append("news_summary should be object")
        return errors

    def validate_prediction_history(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        add_required(errors, data, ["items", "total", "page", "page_size"], "prediction history")
        if not isinstance(data.get("items"), list):
            errors.append("prediction history items should be list")
        return errors

    def validate_backtest_run(self, body: Any) -> list[str]:
        if not is_success_body(body):
            return []
        errors: list[str] = []
        data = as_data(body)
        add_required(errors, data, ["run_id", "run_name", "status", "start_date", "end_date", "created_at", "polling"], "backtest run")
        if isinstance(data.get("polling"), dict):
            add_required(errors, data["polling"], ["status_url", "frames_url", "logs_url", "final_positions_url"], "backtest polling")
        return errors

    def validate_backtest_status(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        add_required(errors, as_data(body), ["run_id", "status", "start_date", "end_date", "trading_days_done", "progress", "final_positions_ready", "error_message"], "backtest status")
        return errors

    def validate_backtest_frames(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        add_required(errors, data, ["run_id", "status", "frames", "next_after_date", "has_more"], "backtest frames")
        if not isinstance(data.get("frames"), list):
            errors.append("frames should be list")
        return errors

    def validate_backtest_day_detail(self, body: Any) -> list[str]:
        if not is_success_body(body):
            return []
        errors: list[str] = []
        add_required(errors, as_data(body), ["run_id", "date", "metrics", "active_positions", "trades", "logs"], "backtest day detail")
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
        add_required(errors, as_data(body), ["run_id", "run_name", "status", "start_date", "end_date", "initial_cash", "benchmark"], "backtest summary")
        return errors

    def validate_final_positions(self, body: Any) -> list[str]:
        if not is_success_body(body):
            return []
        errors: list[str] = []
        data = as_data(body)
        if "positions" not in data or not isinstance(data.get("positions"), list):
            errors.append("final positions.positions should be list")
        return errors

    def validate_crawler_status(self, body: Any) -> list[str]:
        if not is_success_body(body):
            return []
        errors: list[str] = []
        data = as_data(body)
        if not isinstance(data.get("latest_tasks"), list):
            errors.append("crawler latest_tasks should be list")
        if "missing_data_summary" not in data:
            errors.append("crawler missing_data_summary missing")
        return errors

    def validate_stock_universe_status(self, body: Any) -> list[str]:
        if not is_success_body(body):
            return []
        errors: list[str] = []
        data = as_data(body)
        if "latest_sync" not in data:
            errors.append("stock universe latest_sync missing")
        if not isinstance(data.get("source_files"), list):
            errors.append("stock universe source_files should be list")
        return errors

    def validate_daily_refresh_status(self, body: Any) -> list[str]:
        if not is_success_body(body):
            return []
        errors: list[str] = []
        data = as_data(body)
        if "latest_batch" not in data:
            errors.append("daily refresh latest_batch missing")
        if not isinstance(data.get("recent_ticker_tasks"), list):
            errors.append("recent_ticker_tasks should be list")
        return errors

    def validate_logs(self, body: Any) -> list[str]:
        errors = self.validate_success_data(body)
        data = as_data(body)
        add_required(errors, data, ["items", "total", "page", "page_size"], "logs")
        if not isinstance(data.get("items"), list):
            errors.append("logs items should be list")
        return errors

    def validate_admin_users_list(self, body: Any) -> list[str]:
        if not is_success_body(body):
            return []
        errors: list[str] = []
        data = as_data(body)
        add_required(errors, data, ["items", "total", "page", "page_size"], "admin user list")
        if not isinstance(data.get("items"), list):
            errors.append("admin user list items should be list")
        return errors

    def validate_admin_user_detail(self, body: Any) -> list[str]:
        if not is_success_body(body):
            return []
        errors: list[str] = []
        add_required(errors, as_data(body), ["user_id", "username", "role", "status", "created_at", "prediction_count", "backtest_count", "watchlist_count", "recent_operations"], "admin user detail")
        return errors

    def validate_admin_update_status(self, body: Any) -> list[str]:
        if not is_success_body(body):
            return []
        errors: list[str] = []
        add_required(errors, as_data(body), ["user_id", "username", "status", "updated_at"], "admin update status")
        return errors

    def validate_admin_update_role(self, body: Any) -> list[str]:
        if not is_success_body(body):
            return []
        errors: list[str] = []
        add_required(errors, as_data(body), ["user_id", "username", "role", "updated_at"], "admin update role")
        return errors

    def validate_admin_update_username(self, body: Any) -> list[str]:
        if not is_success_body(body):
            return []
        errors: list[str] = []
        add_required(errors, as_data(body), ["user_id", "old_username", "new_username", "updated_at"], "admin update username")
        return errors

    def validate_admin_reset_password(self, body: Any) -> list[str]:
        if not is_success_body(body):
            return []
        errors: list[str] = []
        add_required(errors, as_data(body), ["user_id", "username", "password_updated", "force_logout", "updated_at"], "admin reset password")
        return errors

    def validate_admin_delete_user(self, body: Any) -> list[str]:
        if not is_success_body(body):
            return []
        errors: list[str] = []
        add_required(errors, as_data(body), ["user_id", "username", "deleted", "hard_delete"], "admin delete user")
        return errors

    def _validate_sentiment_counts(self, counts: Any, prefix: str) -> list[str]:
        if not isinstance(counts, dict):
            return [f"{prefix} should be object"]
        errors: list[str] = []
        required = ["window_days", "start_date", "end_date", "news_start_time", "news_end_time", "count_source", "positive_news_count", "negative_news_count", "neutral_news_count", "total_news_count"]
        add_required(errors, counts, required, prefix)
        total = counts.get("total_news_count")
        parts = [counts.get("positive_news_count"), counts.get("negative_news_count"), counts.get("neutral_news_count")]
        if isinstance(total, int) and all(isinstance(x, int) for x in parts) and sum(parts) != total:
            errors.append(f"{prefix}: positive+negative+neutral != total")
        return errors

    # -------------------------
    # Reports
    # -------------------------

    def write_reports(self) -> None:
        ts = now_str()
        json_path = self.output_dir / f"finsight_api_test_report_{ts}.json"
        md_path = self.output_dir / f"finsight_api_test_report_{ts}.md"
        records = [
            {
                "name": r.name,
                "profile": r.profile,
                "method": r.method,
                "url": r.url,
                "expected_status": r.expected_status,
                "request_headers": r.request_headers,
                "request_params": r.request_params,
                "request_json": r.request_json,
                "status_code": r.status_code,
                "elapsed_ms": r.elapsed_ms,
                "ok": r.ok,
                "skipped": r.skipped,
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
            "passed": sum(1 for r in self.records if r.ok and not r.skipped),
            "failed": sum(1 for r in self.records if not r.ok),
            "skipped": sum(1 for r in self.records if r.skipped),
            "created_prediction_id": self.created_prediction_id,
            "created_backtest_run_id": self.created_backtest_run_id,
            "temp_user_id": self.temp_user_id,
            "heavy_endpoints_included": bool(self.args.include_heavy),
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
        lines.append(f"- Skipped: **{summary['skipped']}**")
        lines.append(f"- Created Prediction ID: `{summary['created_prediction_id']}`")
        lines.append(f"- Created Backtest Run ID: `{summary['created_backtest_run_id']}`")
        lines.append("")
        lines.append("## 汇总表")
        lines.append("")
        lines.append("| # | 结果 | 接口 | Profile | 方法 | 状态码 | 耗时 ms | 校验错误 |")
        lines.append("|---:|---|---|---|---|---:|---:|---:|")
        for idx, r in enumerate(self.records, 1):
            result = "⏭ SKIP" if r.skipped else ("✅ PASS" if r.ok else "❌ FAIL")
            lines.append(f"| {idx} | {result} | {r.name} | {r.profile} | {r.method} | {r.status_code} | {r.elapsed_ms} | {len(r.validation_errors)} |")

        lines.append("")
        lines.append("## 详细请求与返回")
        for idx, r in enumerate(self.records, 1):
            result = "SKIP" if r.skipped else ("PASS" if r.ok else "FAIL")
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
        print(f"  Total={summary['total']} Passed={summary['passed']} Failed={summary['failed']} Skipped={summary['skipped']}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Finsight API 自动化测试脚本 v1.5 comprehensive")
    p.add_argument("--base-url", default="http://127.0.0.1:8002", help="后端服务地址")
    p.add_argument("--admin-user", default="admin", help="管理员用户名")
    p.add_argument("--admin-pass", default="Admin123", help="管理员原始密码，脚本会自动转 password_sha256")
    p.add_argument("--user", default="user01", help="普通用户用户名")
    p.add_argument("--user-pass", default="User123", help="普通用户原始密码，脚本会自动转 password_sha256")
    p.add_argument("--output-dir", default="api_test_results", help="测试报告输出目录")
    p.add_argument("--timeout", type=int, default=60, help="单个请求超时时间，秒")

    p.add_argument("--prediction-ticker", default="AAPL", help="预测/股票详情测试 ticker")
    p.add_argument("--prediction-base-date", default="", help="预测基准日；空字符串表示后端自动选择最新可用交易日")
    p.add_argument("--forecast-days", type=int, default=5, choices=[1, 2, 3, 4, 5], help="预测天数，当前后端限制 1~5")
    p.add_argument("--news-window-days", type=int, default=14, help="预测新闻窗口天数")

    p.add_argument("--pipeline-ticker", default="AAPL", help="Data Pipeline coverage ticker")
    p.add_argument("--pipeline-target-date", default="", help="Data Pipeline 目标日期；空表示后端默认")
    p.add_argument("--pipeline-modules", default="market,technical,features", help="Data Pipeline job 模块，仅 include-heavy 时执行")

    p.add_argument("--backtest-tickers", default="AAPL,MSFT,NVDA,TSLA,AMZN,GOOGL,META", help="回测股票池，逗号分隔")
    p.add_argument("--backtest-start", default="2023-01-03", help="回测开始日期")
    p.add_argument("--backtest-end", default="2023-02-28", help="回测结束日期，默认短区间")
    p.add_argument("--initial-cash", type=float, default=10000)
    p.add_argument("--max-position-ratio", type=float, default=0.2)
    p.add_argument("--max-holding-count", type=int, default=5)
    p.add_argument("--fee-rate", type=float, default=0.0005)
    p.add_argument("--take-profit-pct", type=float, default=0.18)
    p.add_argument("--stop-loss-pct", type=float, default=-0.08)

    p.add_argument("--check-old-password-payload", action="store_true", help="额外测试旧版明文 password payload 应返回 422")
    p.add_argument("--include-admin-crud", action="store_true", help="创建临时用户并测试管理员 status/role/username/password/delete")
    p.add_argument("--skip-prediction", action="store_true", help="跳过 POST /api/predictions/run")
    p.add_argument("--skip-backtest", action="store_true", help="跳过 backtest run/status/frames/logs 等接口")
    p.add_argument("--skip-intraday", action="store_true", help="跳过 1D intraday detail")

    p.add_argument("--include-heavy", action="store_true", help="包含最耗时接口：data-pipeline job、daily-refresh run、stock-universe sync")
    p.add_argument("--include-data-pipeline-job", action="store_true", help="只额外执行 data-pipeline job")
    p.add_argument("--include-daily-refresh-run", action="store_true", help="只额外执行 daily-refresh run")
    p.add_argument("--include-stock-universe-sync", action="store_true", help="只额外执行 stock-universe sync")
    p.add_argument("--daily-refresh-target-date", default="", help="每日刷新目标日期；空表示后端默认")

    p.add_argument("--wait-backtest-finish", action="store_true", help="等待回测跑到 finished/failed；默认不等，避免耗时")
    p.add_argument("--backtest-wait-timeout", type=int, default=240, help="等待回测完成的最长秒数")
    p.add_argument("--backtest-poll-interval", type=float, default=3.0, help="等待回测完成时轮询间隔")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    args.prediction_base_date = args.prediction_base_date.strip() or None
    args.pipeline_target_date = args.pipeline_target_date.strip() or None
    args.daily_refresh_target_date = args.daily_refresh_target_date.strip() or None
    FinsightApiTester(args).run_all()


if __name__ == "__main__":
    main()
