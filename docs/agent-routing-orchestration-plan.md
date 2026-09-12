# 智能小车 Agent 路由与任务编排重构方案（草案）

> 状态：**阶段 1 + 阶段 2 已实施（薄路由 + 急停短路 + 灵活 Agent + SummarizationMiddleware + 路由循环编排）**
> 日期：2026-09-02（设计）/ 2026-09-02（阶段 1 实施）
> 关联：当前实现见 [agent-architecture-plan.md](./agent-architecture-plan.md)
> 参考实现：`references/intelligent_customer_service`

## 0. 实施记录（2026-09-02）

阶段 1 已落地，对应改动：

- `agents/car_agent/src/agent/supervisor/graph.py`：supervisor 拆分为 `thin_router`
  （最近 N 条裁剪 + 子图委派 + 急停短路）+ `stop`（确定性急停）+ `flexible_agent`
  （官方 `create_agent` + `SummarizationMiddleware` + 记忆背景注入中间件）。
- `agents/car_agent/src/agent/tools/__init__.py`：工具集合拆分为 `ROUTER_TOOLS`
  （stop_robot + 5 个委派工具）与 `FLEXIBLE_TOOLS`（get_robot_status、recognize_image）。
- `agents/car_agent/pyproject.toml`：新增 `langchain>=1.0,<2.0` 依赖。
- `tests/unit_tests/test_supervisor.py`：按新架构重写，并新增官方
  `SummarizationMiddleware` 端到端压缩测试；全量测试、ruff、mypy 通过。

阶段 2（路由循环编排）已落地：

- `collect_handoff_result` 的出口改回 `thin_router`，形成"子图执行 → 结果 → 路由
  再决策"的循环，支持顺序 + 条件、按上一步结果动态重排。
- `CarAgentState.router_steps` 记录本回合步数，`load_memory` 每个新回合复位为 0；
  `ROUTER_MAX_STEPS`（默认 5）触发后不再咨询路由模型，直接交灵活 Agent 收尾。
- `ROUTER_PROMPT` 新增规则 8：本回合多步时逐次委派，目标完成/失败/取消后停止。
- 新增测试：`test_multi_step_plan_continues_after_first_step_result`（移动→急停
  两步编排）、`test_router_step_cap_forces_finish`（步数上限兜底）。

可调参数（环境变量）：`ROUTING_MESSAGE_COUNT`（默认 10）、
`SUMMARIZE_TRIGGER_TOKENS`（默认 16000）、`SUMMARIZE_KEEP_MESSAGES`（默认 20）、
`ROUTER_MAX_STEPS`（默认 5）。

未决项保持第 8 节所列。

## 1. 背景与动机

原始诉求是"给历史消息加裁剪/摘要，希望使用官方上下文中间件"。排查后发现这背后其实是
一个更根本的架构问题：

- 当前 supervisor 是"**厚路由**"：一个节点既看全量消息历史、又自己选工具、还现场规划执行
  （例如 `delegate_to_motion_workflow` 的 `actions` 列表就是模型现场排出来的）。
- 每轮 `supervisor` 都把整段 `messages` + `memory_context` 塞给 DeepSeek，历史只增不减。
- 近期"卡在 supervisor"的**直接原因**是 DeepSeek 接口慢/超时（实测历史仅 8~10k 字符，并非
  上下文过大）。**本方案不直接解决该根因**，需要正交的缓解手段（超时、流式、最小裁剪兜底）。

因此，本方案的目标是：把"上下文管理"从"在厚路由上打补丁"提升为"按能力拆分路由与执行层，
让上下文按需下沉到子图/agent"，并在此框架内引入官方 `SummarizationMiddleware` 做上下文压缩。

## 2. 现状对照

当前 `agents/car_agent/src/agent/supervisor/graph.py`：

| 组成 | 现状 |
| --- | --- |
| supervisor | `bind_tools(SUPERVISOR_TOOLS)`，全量上下文，模型选唯一工具 |
| 路由 | 模型选工具后跳 `direct_tools` / `prepare_handoff` / `finalize_memory` |
| 固定子图 | motion / follow / location(save·delete) / navigation，带 `interrupt()` 人工确认 |
| 直接工具 | `get_robot_status` / `stop_robot` / `recognize_image` 在 `direct_tools` 内联执行 |
| 记忆 | `load_memory` / `finalize_memory`（profile + episode） |

参考项目 `intelligent_customer_service` 的启示：

- 路由是"薄路由"：`identify_intent` 用结构化输出分类到 `CustomerIntent` 枚举，再由
  纯函数 conditional edge 分发；路由上下文只保留最近 **5** 条 Human/AI 消息。
- 上下文管理是自研的"话题摘要入库"（`node/context.py` + `common/topic_memory.py`），
  **并未使用** `SummarizationMiddleware`。

## 3. 目标架构

```text
                    START → load_memory
                                │
                                ▼
                     thin_router（意图分类 + 急停短路）
                     ┌──────────────┼────────────────┐
                     │              │                │
               固定高成本子图    灵活 agent       普通问答/急停
               ├─ motion        create_agent      （直接短路/直接回答）
               ├─ follow        + Summarization
               ├─ location        Middleware
               └─ navigation     └─ 灵活工具
                     │              │                │
                     └──────────────┴────────────────┘
                                │
                                ▼
                     finalize_memory → END
```

要点：

1. **薄路由**：只看最近 N 条做意图分类 + 分发；`stop_robot` 在路由层直接短路。
2. **固定高成本子图**：motion / follow / location / navigation 保留不动（含确认中断）。
3. **灵活 agent**：`create_agent` + `SummarizationMiddleware`，承载轻量、可扩展工具
   （`get_robot_status`、`recognize_image` 及未来新工具）。
4. **子图级多步编排**（顺序+条件、按结果动态重排）本期**暂缓**。

## 4. 关键决策记录

| # | 决策点 | 结论 | 理由 |
| --- | --- | --- | --- |
| 1 | 路由职责 | 薄路由 + 独立 planner | 路由只管路由；规划/执行下沉 |
| 2 | 工具执行层 | 固定高成本→子图；灵活可扩展→agent | 子图保住确认中断，agent 好扩展 |
| 3 | `stop_robot` | 路由直接短路，不经 planner/agent | 安全紧急，必须最低延迟 |
| 4 | 编排控制流 | 顺序 + 条件分支，按结果动态重排 | 未来任务需根据中间结果调整顺序 |
| 5 | planner 循环实现 | `create_agent` + `SummarizationMiddleware` | 官方 agent loop + 官方上下文压缩 |
| 6 | 子图与 create_agent 对接 | 子图留外层 StateGraph，create_agent 只承载灵活工具 | 避免把带 interrupt 的子图包装成扁平工具 |
| 7 | 推进节奏 | 先产出设计文档，不落代码 | 本稿 |

## 5. 组件设计

### 5.1 薄路由（thin_router）

- 输入：最近 N 条 Human/AI 消息（`ROUTING_MESSAGE_COUNT`，默认参照参考项目取 5，**待定**）。
- 输出：结构化意图枚举（`general_qa` / `motion` / `follow` / `save_location` /
  `delete_location` / `navigate` / `flexible_tool` / `stop`）。
- 分发：纯函数 conditional edge。
- 急停短路：识别到停止意图后**不进入 planner/agent**，直接走 stop 执行路径。
  （具体判定是"关键词/规则拦截"还是"意图枚举中的 stop"**待定**。）

### 5.2 灵活 agent（create_agent）

- 用 `create_agent` 承载轻量工具集合，`SummarizationMiddleware` 压缩其累积历史。
- 工具集合：`get_robot_status`、`recognize_image` + 未来新增（**待定**是否含其它）。
- 不含：`stop_robot`（已短路）、4 个固定子图的委派工具（留在外层）。

### 5.3 固定子图

- motion / follow / location(save·delete) / navigation **保持不变**。
- 继续使用结构化 handoff（`prepare_handoff`）与 `interrupt()` 确认。
- 不接收原始长历史，只接收必要参数。

### 5.4 工具映射（默认，待确认）

| 类别 | 能力 |
| --- | --- |
| 子图（不动） | motion、follow、location(save·delete)、navigation |
| 灵活 agent | get_robot_status、recognize_image、未来新工具 |
| 路由短路 | stop_robot |

## 6. 上下文管理方案

| 层 | 方案 | 状态 |
| --- | --- | --- |
| 薄路由 | 最近 N 条计数裁剪（参考项目模式） | N 待定 |
| 灵活 agent | `SummarizationMiddleware`（复用 DeepSeek 模型做摘要） | 参数待定 |
| 固定子图 | 结构化 handoff，不接原始历史 | 现状 |
| 记忆 | 保持 profile + episode 机制 | 现状 |

`SummarizationMiddleware` 的**具体参数待定**，至少包括：

- 摘要模型：复用 DeepSeek，或独立小模型；
- `max_tokens_before_summary`（触发阈值）；
- `messages_to_keep`（保留最近条数）；
- `token_counter`（精确 or `approximate`）。

## 7. 依赖与运行环境变更

- 新增 `langchain` 主包（`create_agent`、`SummarizationMiddleware` 所在）。
- `agents/car_agent/pyproject.toml` 增加 `langchain>=1.0`。
- 重建 venv / 容器镜像；`langgraph.json` 的 `image_distro: wolfi` 需确认 `langchain` 可安装。

## 8. 风险与未决项

1. **不解决"卡在 supervisor"根因**：薄路由 + flexible agent 反而多一跳 LLM；DeepSeek 慢/超时
   仍需正交缓解（请求超时、流式输出、失败降级）。
2. **灵活工具当前历史很短**，`SummarizationMiddleware` 收益主要是未来扩展与架构规整，当前非紧急。
3. **子图级多步编排**的确认/失败语义未定：顺序 + 条件分支 + 动态重排，叠加"每步间是否人工确认"、
   "某步失败是中止还是回退"两个横切约束。
4. **急停短路的判定方式**（规则/关键词 vs 结构化意图）未定。
5. **迁移影响面**：`supervisor` / `direct_tools` / `prepare_handoff` / `collect_handoff_result`
   需要拆分为 thin_router 与 flexible agent，并保留子图 handoff 簿记。

## 9. 实施分期

- **阶段 0（正交缓解）**：未做——给现有 supervisor 加超时/流式/最小裁剪兜底，先止住"卡住"。
- **阶段 1（已实施）**：薄路由 + 急停短路 + 子图保留 + flexible `create_agent` +
  `SummarizationMiddleware`。
- **阶段 2（已实施）**：路由循环多步编排（顺序 + 条件，按结果动态重排 + 步数上限）。

## 10. 结论

本方案把"裁剪/摘要"的需求，从"在厚路由上临时打补丁"重构为"薄路由 + 固定子图 + 灵活
agent（`create_agent` + 官方 `SummarizationMiddleware`）"的分层架构。路由上下文最小化，
执行上下文按能力下沉，未来任务编排（顺序 + 条件 + 动态重排）有明确落点。实现前需先确认
第 8 节的未决项，并接受"该重构不直接解决 DeepSeek 慢/超时根因"这一前提。
