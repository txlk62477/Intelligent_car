# 智能小车 Agent 架构与当前实现

> 更新时间：2026-09-01<br>
> 对应实现提交：`284c6b45`、`4caef9a6`

## 1. 目标与当前结论

项目已经形成一个可运行的 LangGraph + ROS2 混合架构：LangGraph 负责自然语言理解、固定
Workflow 编排、长期记忆和人工确认；ROS2 负责传感器、定位、Nav2、避障与实时控制；二者
通过本机 Robot Gateway 的 JSON/HTTP interface 连接。

当前已经完成：

- 普通问答、机器人状态查询、立即停车和相机画面理解。
- 经过人工确认的短距离相对移动。
- YOLO 目标解析、确认和限时跟随。
- 用户无感的长期记忆加载与对话结束保存。
- 按机器人和地图强隔离的命名地点教学、更新、删除与召回。
- AMCL 质量检查、目标净空检查、ComputePathToPose 预检和 NavigateToPose 导航。
- Nav2、Agent、人工控制和急停的统一速度仲裁。
- Agent Server 托管 PostgreSQL checkpoints 与 Store，以及 LangSmith Studio Memory 可见性。

建图控制、地图保存、巡逻、复合任务规划和诊断 Agent 仍属于后续范围，本文不会把它们描述为
已有能力。

## 2. 责任分工

- **LangGraph Agent Server**：托管 graph、thread、run、checkpointer 和 Store。
- **Supervisor**：理解用户意图，调用直接 Tool 或委派固定 Workflow，最后组织回复。
- **固定 Workflow**：执行移动、跟随、位置变更和导航等有顺序、有副作用的任务。
- **Robot Gateway**：隐藏 ROS2 Action、Topic、定位质量、地图身份和错误归一化。
- **Nav2 与 Collision Monitor**：负责全局/局部规划、控制和最终碰撞防护。
- **Motion Controller**：执行短距离相对移动和目标跟随，只向 Agent 专用速度话题发布。
- **micro-ROS Agent**：承担 ROS2 与底层单片机之间的 DDS-XRCE 通信。
- **单调 `/clock` 与 TimeMapper**：在 WSL 中提供统一、不回退的 ROS 时间，详见
  [ROS 2 单调时间与仿真时间](./ROS2单调时间与仿真时间.md)。
- **安全规则**：负责限值、超时、冲突急停和人工接管，不交给大模型判断。

LangGraph 项目位于 `agents/car_agent`，与 `software/leap_ros_ws` 分别构建和运行。Agent 代码
不导入 `rclpy`、不创建 ROS Publisher，也不运行 ROS Executor。

## 3. 当前总体结构

```text
用户 / LangSmith Studio / LangGraph SDK
                  │
                  ▼
       官方 LangGraph Agent Server
       ├─ PostgreSQL checkpoints
       └─ PostgreSQL Store / Memory
                  │
          START → load_memory
                  │
                  ▼
              Supervisor
       ┌──────────┼───────────────┐
       │          │               │
  直接 Tools   固定 Workflows   普通问答
  ├─ 状态查询   ├─ 相对移动
  ├─ 急停       ├─ 目标跟随
  └─ 图像理解   ├─ 地图位置教学/删除
               └─ 命名地点 Nav2 导航
       │          │
       └──────┬───┘
              ▼
       finalize_memory → END
              │
              ▼ HTTP/JSON
         Robot Gateway
      ├─ ExecuteMotion Action
      ├─ FollowTarget Action
      ├─ ComputePathToPose Action
      ├─ NavigateToPose Action
      ├─ OccupancyGrid / AMCL
      └─ stop / camera / detections
              │
              ▼
       ROS2 / Nav2 / micro-ROS
```

Supervisor 只选择能力和解释结果。所有运动副作用都位于固定 Workflow 和 Gateway
implementation 中。

## 4. 深模块与 seam

### 4.1 Memory 模块

Memory 模块对主图只暴露两个节点 interface：

```text
load(state, config, runtime)     → 记忆上下文
finalize(state, config, runtime) → 保存结果
```

它的 implementation 隐藏用户身份解析、namespace、语义检索、敏感信息清理、结构化提炼、
Profile 合并、TTL 和 embedding 降级。主图不创建 `PostgresStore` 或 `PostgresSaver`；Agent
Server 根据 `langgraph.json` 自动注入官方 checkpointer 与 Store。

每轮执行顺序固定为：

```text
START
→ 加载完整 Profile 与相关 Episodes
→ 把记忆作为“不可信背景”加入 Supervisor prompt
→ 正常问答或执行 Workflow
→ 提炼本轮稳定事实和摘要
→ 更新 Profile、写入 Episode
→ END
```

不得把 API Key、Token、密码、图片 Base64、原始 Tool JSON、瞬时坐标、速度或历史运动命令
写入用户长期记忆。提炼模型失败时只保存清理后的本轮摘要，不更新稳定 Profile。

### 4.2 LocationStore 模块

`LocationStore` 的 interface 提供当前地图内的位置列举、解析、保存、删除和结果统计。它隐藏
namespace、名称规范化、别名唯一性、语义召回和 embedding 失败降级。

位置资产只保存二维 Nav2 所需字段：

```json
{
  "label": "书桌前",
  "aliases": ["桌边"],
  "pose": {"x": 1.25, "y": -0.50, "yaw": 0.30, "frame_id": "map"},
  "map_id": "sha256:...",
  "robot_id": "xuegecar-01"
}
```

成功/失败次数、最后使用时间和 `needs_review` 可以变化，但导航结果永不自动修正 `x/y/yaw`。
连续失败达到阈值后只标记复核。地图变化时不迁移、不转换、不复用旧坐标。

### 4.3 Workflow 模块

移动、跟随、位置和导航各自是固定 StateGraph。Supervisor 通过结构化 handoff interface 传入
任务参数，Workflow 只返回压缩后的结构化结果；内部计划 ID、轮询状态和 ROS 记录不暴露给
用户。

人工确认使用 LangGraph `interrupt()` 和 checkpoints。中断恢复会从节点开头重放，因此采样、
探测和提交任务等副作用与 `interrupt()` 分处不同节点；恢复后会重新检查安全前提。

### 4.4 RobotGateway seam

Agent 侧依赖 `RobotGateway` Protocol；生产 Adapter 是 `HttpRobotGateway`，测试 Adapter 是
`FakeRobotGateway`。调用方只了解结构化任务和稳定错误码，不了解 ROS2 类型、Executor 或
Action Future。

当前 HTTP interface：

| 方法与路径 | 用途 |
| --- | --- |
| `GET /v1/robot/status` | 融合里程计与活动任务状态 |
| `POST /v1/motions` | 提交短距离原子动作 |
| `GET /v1/motions/{id}` | 查询移动终态 |
| `POST /v1/follow-tasks` | 提交跟随任务 |
| `GET /v1/follow-tasks/{id}` | 查询跟随任务 |
| `POST /v1/follow-tasks/{id}/cancel` | 取消跟随 |
| `GET /v1/navigation/status` | 当前地图、AMCL 位姿与质量 |
| `POST /v1/navigation/preflight` | 静态净空与 ComputePathToPose |
| `POST /v1/navigation-tasks` | 提交 NavigateToPose |
| `GET /v1/navigation-tasks/{id}` | 查询导航终态 |
| `POST /v1/navigation-tasks/{id}/cancel` | 取消导航 |
| `POST /v1/stop` | 取消所有活动任务并急停 |
| `GET /v1/camera/snapshot` | 保存当前相机帧 |
| `GET /v1/perception/detections` | 获取当前 YOLO 检测快照 |

## 5. Checkpoints 与长期记忆

### 5.1 PostgreSQL 托管方式

采用“Agent Server 托管 PostgreSQL 持久化”：Agent Server 负责初始化和维护数据库表，并在
运行时向 graph 注入 checkpointer 和 Store。PostgreSQL 是唯一持久数据源；Redis 只用于运行中
队列、取消和流式事件。

本机数据库使用独立容器、独立 volume，并只绑定 `127.0.0.1:5433`。完整启动方式见
[Agent Server PostgreSQL 持久化](../agents/car_agent/PERSISTENCE.md)。

### 5.2 数据作用域与生命周期

| 数据 | Namespace / 范围 | 生命周期 |
| --- | --- | --- |
| checkpoints | `thread_id` | `keep_latest`，30 天 |
| 用户档案 | `users/<user_id>/profile/current` | 永久 |
| 对话摘要 | `users/<user_id>/episodes/<run_id>` | 180 天 TTL |
| 地图位置 | `robots/<robot_id>/maps/<map_id>/locations/<key>` | 永久，显式删除 |

用户身份优先级：Agent Server 已认证身份 → `configurable.user_id` →
`CAR_AGENT_USER_ID` → `local-user`。机器人身份来自 `configurable.robot_id` 或
`CAR_ROBOT_ID`。

地图位置不属于用户 namespace：多个用户可以为同一机器人和同一地图教学地点，但任何用户都
不能从另一张地图召回坐标。

### 5.3 地图身份

Gateway 对 OccupancyGrid 的以下内容做稳定 SHA-256：

- width、height、resolution；
- origin 的 x、y、yaw；
- 完整 occupancy data。

只要地图几何或任一栅格发生变化，`map_id` 就不同。位置查询只打开当前
`robots/<robot_id>/maps/<map_id>/locations` namespace，不在查询后再依赖模型过滤。

### 5.4 语义索引

Store 使用宿主机 Ollama 的 `qwen3-embedding:0.6b`，维度固定为 1024，索引字段为：

```text
summary, important_facts, label, aliases
```

Profile 不做向量索引；Episodes 和地点可语义检索。Ollama 暂时不可用时，数据仍以
`index=False` 保存，精确名称和别名继续可用。

## 6. 地点教学与导航

### 6.1 教学、更新和删除

只有用户明确说“记住/记录当前位置为某地点”时，Supervisor 才能委派位置 Workflow。普通聊天、
模型推断和历史记忆均不得自动创建坐标。

```text
显式教学请求
→ 读取当前 OccupancyGrid + AMCL pose
→ 校验地图与定位质量
→ 查找当前地图同名/别名位置
→ interrupt 展示 map、x、y、yaw 和更新差异
→ 用户确认
→ 重新读取地图与 pose
→ 地图必须相同，位置移动 ≤ 0.10 m，yaw 变化 ≤ 10°
→ 写入 Store
```

确认前小车若明显移动，或者地图变化，旧确认立即失效。删除同样限制在当前地图并要求确认。

### 6.2 命名地点导航

```text
用户说“走到书桌前”
→ 获取当前 map_id 与 AMCL pose
→ 仅在当前 robot/map namespace 解析标准名称与别名
→ 无精确结果时进行语义召回
→ 多个候选时 interrupt 要求选择
→ 校验 AMCL 新鲜度和协方差
→ 检查目标点及圆形净空区域
→ 调用 ComputePathToPose
→ interrupt 展示地点和 x/y/yaw，单独确认导航
→ 确认后重新检查 map_id
→ 幂等提交 NavigateToPose
→ 轮询终态；超时取消并停车
→ 只更新成功/失败统计，不修改坐标
```

“保存位置”的确认不能代替“开始导航”的确认。Gateway 还拥有独立导航超时，即使 Agent 断连也会
取消超时 goal 并锁止速度输出。

## 7. 移动、跟随与视觉

### 7.1 短距离移动

相对移动接受前进、后退、左转和右转的结构化动作列表。距离、角度和时间有固定范围；缺少数值
时必须询问，超范围直接拒绝。Workflow 确认后通过 `ExecuteMotion` Action 串行执行，不允许模型
循环发布 `/cmd_vel`。

### 7.2 目标跟随

Supervisor 把用户目标转换为单个 YOLO COCO 英文类别。Workflow 先获取检测快照；目标命中时
确认后提交 `FollowTarget`，未命中时列出候选并让用户选择。跟随最长 300 秒，由 ROS 控制节点
闭环发布 Agent 速度。

### 7.3 图像理解

用户提供本地路径时读取允许目录内的图片；询问“当前画面”时由 Gateway 抓取相机最新帧。视觉
模型只描述和判断，不直接控制底盘。当前实现是直接 Tool，不是独立视觉 Agent。

## 8. 统一速度仲裁与安全

所有速度源必须进入 `twist_mux`，不得绕过它直接发布最终 `/cmd_vel`：

```text
Nav2 velocity_smoother → /cmd_vel_nav    (priority 100) ─┐
Motion / Follow       → /cmd_vel_agent  (priority 150) ─┤
人工遥控              → /cmd_vel_teleop (priority 200) ─┼→ twist_mux
急停锁                → /cmd_vel_emergency_lock (255) ─┘
                                                        │
                                                        ▼
                                              /cmd_vel_selected
                                                        │
                                                        ▼
                                               Collision Monitor
                                                        │
                                                        ▼
                                                    /cmd_vel
```

Jazzy 的 `twist_mux` 已显式配置 `use_stamped: false`，与当前 Motion Controller 和 Nav2 的
`geometry_msgs/Twist` 一致；输出使用绝对 remap `/cmd_vel_out → /cmd_vel_selected`。

统一仲裁规则：

1. 急停锁 255：遮蔽所有速度源；Gateway 每 0.1 秒发送心跳，心跳超时也进入锁止。
2. 人工控制 200：覆盖 Agent 与 Nav2。
3. Agent 移动/跟随 150：覆盖 Nav2。
4. Nav2 100：最低自主控制优先级。
5. Gateway 全局任务槽不允许移动、跟随和导航并行。
6. 若检测到 `/cmd_vel_agent` 与 `/cmd_vel_nav` 同时存在非零指令，按冲突处理并触发急停锁。
7. Collision Monitor 位于 mux 下游，是到达底盘前的最终碰撞防护。
8. `POST /v1/stop` 无需模型确认，会取消活动 Action、触发急停并保持最高优先级锁。

运行时应使用以下命令审计最终速度话题，确保 `/cmd_vel` 只有 Collision Monitor 一个 publisher：

```bash
ros2 topic info /cmd_vel -v
```

## 9. 当前完成矩阵

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| Supervisor 普通问答 | 已完成 | 默认中文回复 |
| 状态查询、急停 | 已完成 | 直接 Tool |
| 相对移动 Workflow | 已完成 | 限值、确认、幂等、超时 |
| 目标跟随 Workflow | 已完成 | YOLO 候选、确认、取消 |
| 当前画面理解 | 已完成 | Gateway snapshot + 视觉模型 |
| PostgreSQL checkpoints | 已完成 | Agent Server 默认 backend，30 天 |
| 用户长期记忆 | 已完成 | Profile + 180 天 Episodes |
| LangSmith Studio Memory | 已接入 | 使用 Agent Server Store namespaces |
| 地图位置教学/删除 | 已完成 | robot/map 隔离，确认后写入 |
| 命名地点 Nav2 导航 | 已完成 | 预检、确认、执行、统计 |
| 统一速度仲裁 | 已完成 | 100/150/200/255 + Collision Monitor |
| 建图和地图保存 Workflow | 未实现 | 后续范围 |
| 巡逻与复合任务 Workflow | 未实现 | 后续范围 |
| 任务规划/诊断 Agent | 未实现 | 有真实需求后再增加 seam |
| 电量状态 | 未实现 | Gateway 当前未接入电量话题 |
| 多机器人调度 | 未实现 | 目前只完成数据作用域隔离 |

## 10. 验证状态

截至本文更新时间：

- Agent 单元测试：58 passed。
- Agent Ruff：通过。
- Agent mypy：通过。
- Gateway HTTP、地图指纹、AMCL 质量和栅格净空测试：通过。
- `xuegecar_agent_bridge` 与 `xuegecar_navigation2`：colcon build 通过。
- 当前两个 ROS 包测试：25 passed。
- PostgreSQL 17 `pgvector/pgvector:pg17` 容器：健康，绑定 `127.0.0.1:5433`。
- `ros-jazzy-twist-mux`：已安装，配置可正确加载四个输入及其消息类型。

仍需在真实整车运行时完成以下验收：

1. 在 Agent Server + LangSmith Studio 中跨 thread 验证 Profile、Episodes 和 Locations 可见并可召回。
2. 在 Ollama 实际运行时验证 `qwen3-embedding:0.6b` 的 1024 维索引。
3. 用真实 AMCL 与地图验证位置教学、地图切换隔离和 NavigateToPose 到达精度。
4. 同时注入各速度源，验证 mux 优先级、冲突锁和 Collision Monitor 的完整 DDS 数据链。
5. 审计最终 `/cmd_vel` publisher，确认没有任何绕过仲裁的节点。

## 11. 后续演进路线

1. **真实闭环验收**：完成上述 Studio、地图、导航和速度链验证。
2. **建图 Workflow**：启动/停止 SLAM、保存地图、切换地图；所有覆盖操作必须确认。
3. **有限复合任务**：实现“导航—观察—返回”，仍由固定 Workflow 编排。
4. **巡逻与异常报告**：加入执行预算、取消、断点恢复和人工接管。
5. **诊断能力**：积累真实失败样本后，再决定是否增加诊断 Agent。

适合作为下一阶段首个整车闭环：

> 用户明确教学“这里是书桌前”，确认保存；切换新 thread 后说“走到书桌前”，系统从当前地图
> 的长期记忆召回坐标，完成定位与路径预检，再次确认后通过 Nav2 到达并停车。

该场景同时验证 PostgreSQL 长期记忆、LangSmith Memory、地图隔离、interrupt/checkpoint 恢复、
Nav2、统一速度仲裁和执行反馈。

## 12. 参考文档

- [LangGraph Agent Server](https://docs.langchain.com/langsmith/agent-server)
- [LangGraph CLI 与 Store 配置](https://docs.langchain.com/langsmith/cli)
- [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [LangGraph Long-term memory](https://docs.langchain.com/oss/python/langchain/long-term-memory)
- [LangGraph Semantic search](https://docs.langchain.com/langsmith/semantic-search)
- [twist_mux](https://docs.ros.org/en/jazzy/p/twist_mux/)
