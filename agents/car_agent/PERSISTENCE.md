# Agent Server PostgreSQL 持久化

本项目使用 LangGraph Agent Server 官方托管的 checkpointer 与 Store。Agent 代码不创建
`PostgresSaver`/`PostgresStore`：Agent Server 根据 `langgraph.json` 初始化表、注入
checkpointer 和 Store，并让 Studio 的 **Memory** 面板直接读写同一 Store。

## 数据边界

- checkpoints：每个 `thread_id` 的短期对话状态，`keep_latest`，默认保留 30 天。
- `users/<user_id>/profile/current`：永久用户档案，每轮自动加载、结束时自动合并保存。
- `users/<user_id>/episodes/<run_id>`：每轮摘要，TTL 180 天。
- `robots/<robot_id>/maps/<map_id>/locations/<key>`：永久地图位置资产。
  `map_id` 是 OccupancyGrid 尺寸、分辨率、原点和完整栅格数据的 SHA-256；不同地图绝不混用。

位置资产只保存 `x`、`y`、`yaw`（`frame_id=map`）。只有用户明确教学并在中断点确认后
才会写入；导航前还会检查 AMCL 新鲜度/协方差、目标净空、ComputePathToPose，并再次确认。

## 本机启动

1. 复制 `.env.example` 为 `.env`，至少配置 DeepSeek 与 LangSmith 密钥，并修改本地数据库密码。
2. 确保宿主机 Ollama 已加载 `qwen3-embedding:0.6b`。Agent Server 容器通过
   `host.docker.internal:11434` 调用它，向量维度固定为 1024。
3. 启动专用数据库：

   ```bash
   docker compose --env-file .env -f docker-compose.postgres.yml up -d
   ```

4. 启动官方 Agent Server，并显式连接该 PostgreSQL：

   ```bash
   langgraph up --no-pull --wait \
     --postgres-uri "postgresql://car_agent:你的密码@host.docker.internal:5433/car_agent"
   ```

5. 在 LangSmith Studio 打开 `car_agent`。以同一 `user_id` 发起多轮/多 thread 对话后，
   Memory 面板会显示上述 namespaces；新一轮会在调用模型前自动加载 profile 和相关 episodes。

`configurable.user_id` 可用于本地测试。生产环境应由 Agent Server 的认证身份提供用户 ID；
未提供时才降级为 `CAR_AGENT_USER_ID`。地图位置不属于用户 namespace，而属于机器人和地图，
所以多用户可以教同一台车，但不能让一张图的位置泄漏到另一张图。

数据库只绑定 `127.0.0.1`，volume 名为 `car_agent_postgres_data`。删除该 volume 会永久删除
checkpoints 与长期记忆，日常重启不要执行 `docker compose down -v`。
