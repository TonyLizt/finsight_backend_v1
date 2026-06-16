# Finsight FastAPI backend Docker image
# 使用 Python 3.10，和本项目推荐的 conda 环境版本一致。
FROM python:3.11-slim

# 避免生成 .pyc，并让日志实时输出到终端。
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 安装少量系统依赖。gcc 用于个别 Python 包编译；curl 用于容器健康检查。
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc curl \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖文件，利用 Docker 构建缓存。
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip \
    && pip install -r /app/requirements.txt

# 复制项目代码。
COPY . /app

# 启动脚本负责等待 MySQL、初始化演示数据、启动 FastAPI。
RUN chmod +x /app/docker/entrypoint.sh

EXPOSE 8000

CMD ["/app/docker/entrypoint.sh"]
