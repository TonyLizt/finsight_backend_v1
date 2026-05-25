"""本地启动入口。

用法：
    python run.py
或：
    uvicorn app.main:app --reload
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
