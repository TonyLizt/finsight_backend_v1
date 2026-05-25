# Finsight Backend Docker 部署说明

本 Docker 配置会同时启动：

- `finsight_mysql`：MySQL 8.0 数据库
- `finsight_backend`：FastAPI 后端服务

后端通过 Docker 内部网络访问数据库，连接地址为：

```env
DATABASE_URL=mysql+pymysql://finsight_user:finsight_password@db:3306/finsight?charset=utf8mb4
```

## 1. 第一次启动

在项目根目录执行：

```bash
docker compose up -d --build
```

查看日志：

```bash
docker compose logs -f backend
```

启动成功后访问：

```text
http://127.0.0.1:8000/docs
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

## 2. 默认演示账号

如果 `.env.docker` 中 `RUN_SEED=1`，容器启动时会自动写入演示数据。

```text
管理员：admin / Admin123
普通用户：user01 / User123
```

## 3. MySQL 连接方式

宿主机访问 MySQL：

```bash
mysql -h 127.0.0.1 -P 3307 -u finsight_user -p finsight
```

密码：

```text
finsight_password
```

注意：宿主机端口使用 `3307`，避免与本机已有 MySQL 的 `3306` 冲突。

## 4. 常用命令

停止服务：

```bash
docker compose down
```

停止并删除数据库数据卷，完全重置数据库：

```bash
docker compose down -v
```

重新构建后端镜像：

```bash
docker compose up -d --build backend
```

进入后端容器：

```bash
docker exec -it finsight_backend bash
```

手动执行初始化演示数据：

```bash
docker exec -it finsight_backend python -m app.scripts.seed_demo
```

查看数据库容器日志：

```bash
docker compose logs -f db
```

## 5. 修改配置

Docker 环境变量在 `.env.docker` 中配置。

如果部署到服务器，请至少修改：

```env
SECRET_KEY=change-this-secret-key-in-real-deployment
```

如需修改数据库账号密码，要同时修改：

- `docker-compose.yml` 中 `MYSQL_USER`、`MYSQL_PASSWORD`
- `.env.docker` 中 `DATABASE_URL`

## 6. 当前版本说明

当前第一版后端中：

- 数据库表会通过 SQLAlchemy `create_all` 自动创建。
- `seed_demo` 会写入演示用户、AAPL 示例行情、新闻和模型版本。
- 大模型报告和真实回测引擎暂为占位逻辑，后续可在 `app/services` 中替换。
