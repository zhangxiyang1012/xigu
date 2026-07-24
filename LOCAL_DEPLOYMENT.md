# 析股本地部署

## 准备

1. 安装并启动 Docker Desktop。
2. 复制 `.env.example` 为 `.env`，修改 `POSTGRES_PASSWORD`。
3. 在项目目录执行：`docker compose up -d --build`。
4. 浏览器访问 `http://localhost:3000`；API 健康检查为 `http://localhost:8000/health`。

PostgreSQL 仅监听 `127.0.0.1:5432`，不会暴露给局域网或互联网。数据保存在 Docker 卷 `xigu_postgres` 中，容器升级或重建不会删除数据。

## 数据同步

- 浏览股票列表时会保存当日快照。
- 打开个股时会回填该股票可用历史日线。
- 每个交易日 18:10 自动执行全市场当日快照同步。
- 手动触发：`curl -X POST http://localhost:8000/api/sync/daily`。

## 备份

`docker compose exec postgres pg_dump -U xigu -d xigu -Fc -f /tmp/xigu.dump`

将备份复制到本机：`docker compose cp postgres:/tmp/xigu.dump ./xigu.dump`

## 停止

`docker compose down`。不要添加 `-v`，否则会删除数据库卷。
