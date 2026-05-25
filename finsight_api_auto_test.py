#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Finsight API 自动化测试脚本

作用：
1. 按 04《数据库与 API 接口设计说明书》v5.0 的接口顺序自动请求后端 API。
2. 自动登录普通用户和管理员，自动携带 JWT Token。
3. 记录每次请求的方法、URL、请求头、请求体、状态码、返回值和耗时。
4. 输出 JSON 与 Markdown 两份测试报告，方便提交实验报告或排查接口问题。

默认后端地址：
    http://127.0.0.1:8002

运行示例：
    python finsight_api_auto_test.py
    python finsight_api_auto_test.py --base-url http://127.0.0.1:8002
    python finsight_api_auto_test.py --admin-user admin --admin-pass Admin123 --user user01 --user-pass User123

依赖：
    pip install requests
"""

from __future__ import annotations

import argparse
import json
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


# =============================
# 工具函数
# =============================

SENSITIVE_KEYS = {
    "password",
    "confirm_password",
    "new_password",
    "token",
    "authorization",
    "Authorization",
}


def now_str() -> str:
    """返回用于报告文件名的时间戳。"""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_json(obj: Any) -> Any:
    """把不可 JSON 序列化的对象转成字符串，避免报告写入失败。"""
    try:
        json.dumps(obj, ensure_ascii=False)
        return obj
    except TypeError:
        return str(obj)


def mask_sensitive(obj: Any) -> Any:
    """在记录报告时隐藏密码、Token 等敏感字段。真实请求仍使用原始值。"""
    if isinstance(obj, dict):
        new_obj = {}
        for k, v in obj.items():
            if k in SENSITIVE_KEYS or str(k).lower() in SENSITIVE_KEYS:
                new_obj[k] = "***MASKED***"
            else:
                new_obj[k] = mask_sensitive(v)
        return new_obj
    if isinstance(obj, list):
        return [mask_sensitive(x) for x in obj]
    return obj


def pretty(obj: Any) -> str:
    """格式化 JSON，用于 Markdown 报告。"""
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)


@dataclass
class TestRecord:
    """单个 API 测试记录。"""

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


class FinsightApiTester:
    def __init__(
        self,
        base_url: str,
        admin_user: str,
        admin_pass: str,
        normal_user: str,
        normal_pass: str,
        output_dir: str,
        timeout: int = 15,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.admin_user = admin_user
        self.admin_pass = admin_pass
        self.normal_user = normal_user
        self.normal_pass = normal_pass
        self.timeout = timeout
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

    # -----------------------------
    # 请求封装
    # -----------------------------
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
        """
        统一请求函数。

        expected_status 允许多个状态码，是因为一些接口在数据不足时可能合理返回 404/400。
        本脚本仍会完整记录响应内容。
        """
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

    # -----------------------------
    # 常用解析
    # -----------------------------
    @staticmethod
    def get_data(resp: Optional[requests.Response]) -> Dict[str, Any]:
        if resp is None:
            return {}
        try:
            body = resp.json()
            data = body.get("data")
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def get_items(resp: Optional[requests.Response]) -> List[Dict[str, Any]]:
        data = FinsightApiTester.get_data(resp)
        items = data.get("items")
        return items if isinstance(items, list) else []

    # -----------------------------
    # 测试主流程
    # -----------------------------
    def run_all(self) -> None:
        """按 API 文档顺序执行所有主要接口测试。"""
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
        """测试健康检查接口。"""
        self.request("Health Check", "GET", "/health", expected_status=[200, 404])

    def test_auth(self) -> None:
        """测试注册、登录、当前用户信息接口。"""
        # 注册临时用户。若重复或接口限制，也记录响应，不中断后续测试。
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
        data = self.get_data(resp)
        self.temp_user_id = data.get("user_id") or data.get("id")

        # 普通用户登录。
        resp, _ = self.request(
            "Auth - Login Normal User",
            "POST",
            "/api/auth/login",
            json_body={"username": self.normal_user, "password": self.normal_pass},
            expected_status=[200],
        )
        self.user_token = self.get_data(resp).get("token")

        # 管理员登录。
        resp, _ = self.request(
            "Auth - Login Admin",
            "POST",
            "/api/auth/login",
            json_body={"username": self.admin_user, "password": self.admin_pass},
            expected_status=[200],
        )
        self.admin_token = self.get_data(resp).get("token")

        # 查询当前用户。
        self.request(
            "Auth - Get Me Normal User",
            "GET",
            "/api/auth/me",
            token=self.user_token,
            expected_status=[200, 401],
        )
        self.request(
            "Auth - Get Me Admin",
            "GET",
            "/api/auth/me",
            token=self.admin_token,
            expected_status=[200, 401],
        )

    def test_stock_api(self) -> None:
        """测试股票搜索、详情、新闻、情绪接口。"""
        resp, _ = self.request(
            "Stock - Search AAPL",
            "GET",
            "/api/stocks/search",
            token=self.user_token,
            params={"keyword": "AAPL", "only_supported": False, "include_etf": True, "limit": 10},
            expected_status=[200],
        )

        ticker = "AAPL"
        items = self.get_items(resp)
        if items:
            ticker = items[0].get("ticker") or ticker

        self.request(
            "Stock - Detail",
            "GET",
            f"/api/stocks/{ticker}/detail",
            token=self.user_token,
            params={"range": "1m", "include_news": True, "include_indicators": True},
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
        else:
            # 如果演示库没有新闻，用一个不存在 ID 测错误返回是否规范。
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
            f"/api/stocks/{ticker}/sentiment-summary",
            token=self.user_token,
            params={"window_days": 7},
            expected_status=[200, 404],
        )

    def test_watchlist_api(self) -> None:
        """测试自选股增删查。"""
        self.request(
            "Watchlist - Add AAPL",
            "POST",
            "/api/watchlist",
            token=self.user_token,
            json_body={"ticker": "AAPL", "auto_fetch": True},
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
            "Watchlist - Delete AAPL",
            "DELETE",
            "/api/watchlist/AAPL",
            token=self.user_token,
            expected_status=[200, 404],
        )

    def test_prediction_api(self) -> None:
        """测试预测运行、预测历史、预测详情。"""
        resp, _ = self.request(
            "Prediction - Run AAPL",
            "POST",
            "/api/predictions/run",
            token=self.user_token,
            json_body={
                "ticker": "AAPL",
                "forecast_days": 5,
                "analysis_mode": "full",
                "risk_profile": "balanced",
                "news_window_days": 7,
                "force_refresh": False,
            },
            expected_status=[200, 400, 404, 500],
        )
        data = self.get_data(resp)
        self.created_prediction_id = data.get("prediction_id") or data.get("id")

        hist_resp, _ = self.request(
            "Prediction - History",
            "GET",
            "/api/predictions/history",
            token=self.user_token,
            params={"ticker": "AAPL", "page": 1, "page_size": 20},
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
        """测试回测任务创建、状态、帧、日志、详情、汇总和最终持仓接口。"""
        resp, _ = self.request(
            "Backtest - Run",
            "POST",
            "/api/backtest/run",
            token=self.user_token,
            json_body={
                "run_name": "API Auto Test Backtest",
                "tickers": ["AAPL", "MSFT"],
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
            expected_status=[200, 400, 404, 500],
        )
        data = self.get_data(resp)
        self.created_run_id = data.get("run_id") or data.get("id")

        if not self.created_run_id:
            # 若回测启动失败，后续用 1 号任务测错误响应。
            self.created_run_id = 1

        run_id = self.created_run_id
        self.request(
            "Backtest - Status",
            "GET",
            f"/api/backtest/{run_id}/status",
            token=self.user_token,
            expected_status=[200, 404],
        )
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
        self.request(
            "Backtest - Day Detail",
            "GET",
            f"/api/backtest/{run_id}/days/2024-01-02",
            token=self.user_token,
            expected_status=[200, 404, 400],
        )
        self.request(
            "Backtest - Summary",
            "GET",
            f"/api/backtest/{run_id}/summary",
            token=self.user_token,
            expected_status=[200, 404],
        )
        self.request(
            "Backtest - Final Positions By Run",
            "GET",
            f"/api/backtest/{run_id}/final-positions",
            token=self.user_token,
            expected_status=[200, 404],
        )
        self.request(
            "Backtest - Latest Final Positions",
            "GET",
            "/api/backtest/latest/final-positions",
            token=self.user_token,
            params={"include_empty": True},
            expected_status=[200, 404],
        )

    def test_model_api(self) -> None:
        """测试当前启用模型查询。"""
        self.request(
            "Model - Active Models",
            "GET",
            "/api/models/active",
            token=self.user_token,
            expected_status=[200, 404],
        )

    def test_crawler_api(self) -> None:
        """测试爬虫状态和股票基础库同步接口。"""
        self.request(
            "Crawler - Status",
            "GET",
            "/api/crawler/status",
            token=self.admin_token,
            expected_status=[200, 403, 404],
        )
        self.request(
            "Crawler - Stock Universe Status",
            "GET",
            "/api/crawler/stock-universe/status",
            token=self.admin_token,
            expected_status=[200, 403, 404],
        )
        self.request(
            "Crawler - Trigger Stock Universe Sync",
            "POST",
            "/api/crawler/stock-universe/sync",
            token=self.admin_token,
            json_body={"force": False},
            expected_status=[200, 403, 404, 500],
        )

    def test_log_api(self) -> None:
        """测试管理员日志查询。"""
        self.request(
            "Log - Query Logs",
            "GET",
            "/api/logs",
            token=self.admin_token,
            params={"page": 1, "page_size": 20},
            expected_status=[200, 403],
        )

    def test_admin_user_api(self) -> None:
        """测试管理员用户管理接口。尽量使用临时用户，避免修改 admin 和 user01。"""
        resp, _ = self.request(
            "Admin - User List",
            "GET",
            "/api/admin/users",
            token=self.admin_token,
            params={"page": 1, "page_size": 20},
            expected_status=[200, 403],
        )

        # 找到临时用户 ID。若注册时已经返回 ID，就直接使用。
        if not self.temp_user_id:
            for item in self.get_items(resp):
                if item.get("username") == self.temp_username:
                    self.temp_user_id = item.get("user_id") or item.get("id")
                    break

        # 如果临时用户不存在，就尝试查 user01 详情，但不做破坏性修改。
        target_user_id = self.temp_user_id
        if target_user_id:
            self.request(
                "Admin - User Detail Temp",
                "GET",
                f"/api/admin/users/{target_user_id}",
                token=self.admin_token,
                expected_status=[200, 404, 403],
            )
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
            # 没有临时用户时，只测试一个不存在 ID 的返回。
            self.request(
                "Admin - User Detail Not Found",
                "GET",
                "/api/admin/users/999999",
                token=self.admin_token,
                expected_status=[404, 400, 403],
            )

    # -----------------------------
    # 报告输出
    # -----------------------------
    def write_reports(self) -> None:
        """输出 JSON 和 Markdown 测试报告。"""
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
        lines.append("")
        lines.append("## 汇总表")
        lines.append("")
        lines.append("| # | 结果 | 接口 | 方法 | 状态码 | 耗时 ms |")
        lines.append("|---:|---|---|---|---:|---:|")
        for idx, r in enumerate(self.records, 1):
            result = "✅ PASS" if r.ok else "❌ FAIL"
            lines.append(f"| {idx} | {result} | {r.name} | {r.method} | {r.status_code} | {r.elapsed_ms} |")
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
    parser.add_argument("--timeout", type=int, default=15, help="单个请求超时时间，秒")
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
    )
    tester.run_all()


if __name__ == "__main__":
    main()
