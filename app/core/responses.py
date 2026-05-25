"""统一响应格式工具。"""

from typing import Any


def ok(data: Any = None, message: str = "ok") -> dict:
    """成功响应。"""
    return {"success": True, "data": data if data is not None else {}, "message": message}


def fail(error_code: str, message: str, data: Any = None) -> dict:
    """失败响应。一般通过 HTTPException 抛出，这里保留给业务层复用。"""
    payload = {"success": False, "error_code": error_code, "message": message}
    if data is not None:
        payload["data"] = data
    return payload
