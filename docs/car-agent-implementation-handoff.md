# car_agent 混合智能体改造交接说明

日期：2026-09-13
状态：**阶段一至阶段三主体及本轮审查修复已完成；单元和静态检查通过，真实宿主/车辆待验收；阶段四待办。**
设计依据：[car-agent-design.md](./car-agent-design.md)。

## 1. 完成范围

本轮按设计文档完成“观察 → 判断 → 确认 → 执行”的编排闭环，并落实代码级执行边界。
已与用户确认的关键决策全部落地：

| 决策 | 落地位置 |
| --- | --- |
| 移除薄路由，Agent 首次判断 | `supervisor/graph.py` 主图拓扑 |
| 官方 `create_agent` + `SummarizationMiddleware` 继续使用 | `_build_flexible_agent` |
| Agent 中间执行意图不展示，只有 Workflow 预检与确认可见 | `ControlBoundaryMiddleware` 截获控制调用 |
| 移动/跟随/导航必须进入固定 Workflow 并取得确认 | 四个固定 Workflow |
| 跟随候选选择与执行确认分两步 | `workflows/follow/graph.py` |
| 自然语言停车走受限请求，代码直接进入 `stop` | `ControlBoundaryMiddleware` + `SupervisorNodes.stop` |
| `awaiting_input` 延续原 `task_id`，终态后新建任务 | `SupervisorNodes.initialize_task` |
| 预算：委派 ≤5、单步恢复/重试 ≤1、观察 ≤3 | 主图常量 + 编排入口 |
| `source_observation_ids` 必须属于当前任务 | `SupervisorNodes.orchestrate_request` |
| 提交超时用同一 `operation_id` 查询，否则 `execution_unknown` | `workflows/recovery.py` + 三个 Workflow |
| 不兼容旧 checkpoint，缺版本号或版本不匹配即拒绝 | `supervisor/checkpoints.py` 调用入口预检 + 初始化防御检查 |
| 静态描述表统一 Workflow 接入 | `supervisor/workflow_registry.py` |
| 总目标下展示已完成、当前和待完成步骤 | `supervisor/progress.py` 的 `task_progress` 投影 |
| 剩余目标建议只在对应步骤成功后应用 | `SupervisorNodes.collect_handoff_result` |
| 删除/覆盖确认绑定地点对象快照 | `workflows/location/graph.py` + `LocationStore` |
| 非法模型参数返回澄清而非直接异常 | `WorkflowSubmission` 请求信封校验 |

## 2. 当前代码结构

```text
src/agent/
├─ supervisor/graph.py       主图：任务初始化、可信上下文注入、控制边界、编排入口、终态解释
├─ supervisor/workflow_registry.py  静态接入描述（参数模型、准备器、目标节点、结果字段）
├─ supervisor/checkpoints.py 主图普通调用与中断恢复之前拒绝旧 checkpoint（版本 3）
├─ supervisor/progress.py    总目标和步骤列表的公共展示投影
├─ tools/
│  ├─ __init__.py            AGENT_TOOLS（只读 + 控制）与 READ_ONLY_TOOLS
│  ├─ requests.py            所有严格请求模型（Motion/Follow/Save/Delete/Navigation）
│  ├─ orchestration.py       request_workflow / ask_user 的 schema 与参数形状说明
│  ├─ robot.py               get_robot_status / stop_robot
│  └─ vision.py              recognize_image
├─ state/car_agent.py        任务记录、结构化结果类型（WorkflowStatus/SimpleStatus）
└─ workflows/
   ├─ recovery.py            提交结果未知时的幂等恢复协议（查询/未提交/状态未知）
   ├─ motion/graph.py        计划确认 → 串行执行 → 终态归一化
   ├─ follow/graph.py        探测 → 候选选择 → 执行确认 → 提交轮询
   ├─ location/graph.py      地点保存/删除（确认后重检地图与位姿）
   └─ navigation/graph.py    地点解析 → 预检 → 确认 → Nav2 执行
```

已删除的旧实现：`tools/perception.py`、`tools/navigation.py` 中的 `delegate_to_*` 委派工具、
`ROUTER_PROMPT`、`router_steps` 状态字段、`FLEXIBLE_TOOLS`/`ROUTER_TOOLS`/`DELEGATION_TOOLS`
分组，以及 Workflow 中不再被读取的 `*_tool_call_id` 交接字段。

## 3. 关键实现点

### 3.1 代码边界先于模型

- **单工具约束**：模型一次返回多个工具调用时，全部拒绝并逐个生成配对 ToolMessage，
  不执行其中任何一个，任务直接进入终态解释。
- **观察预算**：只读工具在工具执行前拦截；超预算时既不发起调用，也不再多走一轮模型。
- **澄清期封锁**：`awaiting_input` 状态下再次请求执行会被拒绝，并以确定性文案结束本轮，
  不会出现“模型反复重试同一步”。
- **终态无工具**：终态解释节点只调用模型生成说明，不绑定任何工具。

### 3.2 稳定的步骤与操作标识

主图把 `step_id`（`task_id:序号`）作为 `*_plan_id` 下发给子图，子图据此生成
`operation_id`（`<step_id>:<动作下标>`、`follow-<step_id>`、`nav-<step_id>`）。
同一步骤在 checkpoint 恢复时复用同一批 `operation_id`，由 Gateway 幂等识别，
不会重复运动；用户明确要求的两次相同动作仍是两个不同步骤，允许都执行。

### 3.3 提交结果未知协议

`workflows/recovery.py` 统一实现：提交异常后**只用同一个 `operation_id` 查询**。

| 查询结论 | 处理 |
| --- | --- |
| 查到记录 | 继续轮询原任务（不重新提交） |
| `NOT_FOUND` | 报告失败（`SUBMIT_NOT_FOUND`，明确“没有该操作记录”） |
| 查询仍失败 | 尽力停车后返回 `execution_unknown`，任务进入终态解释 |

提交成功后轮询失败同样返回 `execution_unknown`，不再笼统报 `failed`。

### 3.4 上下文与工具消息

- 每轮注入代码生成的可信任务状态；长期记忆标注为不可信背景。
- `_trim_preserving_tool_pairs` 先把 AI 调用及其全部 ToolMessage 分为完整组，再按组裁剪；
  不只回补 AI 消息。不完整、重复结果的工具组和孤立结果整组丢弃，不编造结果。
- `SummarizationMiddleware` 继续负责长对话压缩。

### 3.5 进度展示与确认绑定

- 请求增加 `step_description` 与 `remaining_goals_after_success`，绑定到代码创建的当前步骤。
- 仅成功结果更新 `completed_steps` 和剩余目标；失败、取消和执行未知不应用建议。
- 主图输出增加 `task_progress`，包含 `goal/status/steps/completed_count/stop_reason`；
  前端可以渲染“总目标 → 步骤列表”，本轮未修改网页 UI。
- 地点变更确认绑定完整快照，提交前重检对象及名称/别名；变化则原确认作废并停止变更。
- Store 没有原子 CAS，尚不保证跨进程最后一次检查与写入之间的事务隔离。

## 4. 验收结果

| 检查 | 结果 |
| --- | --- |
| 本轮单元测试 | **135 passed**（含新增 41 项回归），最终复跑 40.80 秒 |
| `ruff check src tests` | 通过 |
| `ruff format --check src tests` | 通过（57 files already formatted） |
| `mypy src` | 通过（42 source files） |
| 真实模型/车辆集成 | 本轮未运行，不计入当前验收 |

测试条件：沙箱内最小 `asyncio.to_thread` 也会停滞。测试进程临时把 selector 单次等待上限
设为 50 ms 后完成上述测试，未修改生产线程调用。沙箱外测试请求因审批服务连接中断未获
批准；需要在正常宿主复跑标准 `pytest tests/unit_tests -q`。2026-09-12 的“96 passed
（94 单元 + 2 真实模型）”是历史记录，不代表新增协议已经过真实模型验收。

交付前验收清单（原第 5 节）逐项对应测试：

- [x] 本轮全量单元测试通过（测试运行器条件见上文）。
- [ ] 正常宿主标准运行器与新增协议的真实模型集成复核。
- [x] `ruff check src tests` 和格式检查通过。
- [x] 普通问答不访问运动接口。
- [x] 查询状态/识别图片只产生只读调用和观察记录。
- [x] 看到杯子后跟随：观察 → Workflow 实时检测 → 候选选择 → 执行确认。
- [x] 每个移动操作执行前均有用户确认。
- [x] 用户取消、Workflow 失败、状态未知和预算耗尽后不会再次调用 Gateway。
- [x] 提交超时不会盲目生成新 operation ID。
- [x] 同一 checkpoint 恢复不会重复运动；用户明确要求两次相同动作仍允许两步执行。
- [x] Agent 输出多个控制调用时，全部拒绝且消息完整配对。
- [x] 终态解释阶段无任何工具能力。
- [x] 旧 checkpoint 被拒绝，新会话正常工作。

## 5. 剩余工作

### 5.1 阶段四（未开始）

1. 显式停止事件：核实 Agent Server 与 Gateway 契约后，让 UI/调用端的停止事件绕过模型
   直接进入停止处理器，同时把当前任务标记取消并阻断后续步骤。
2. 运行中取消与“提交未返回”竞争的处理，以及在途 Workflow 的取消协调。
3. 性能：记录各阶段耗时，再决定是否优化普通问答的模型调用次数。
4. 停止响应区分“已发出请求”和“已确认停止”。

### 5.2 真机验收（未开始）

单元测试与假 Gateway 不能替代真机验证，仍需在整车上完成：

1. 真实 AMCL/地图下的地点教学、地图隔离与 NavigateToPose 精度。
2. 各速度源同时注入，验证 `twist_mux` 优先级、冲突锁与 Collision Monitor 数据链。
3. `ros2 topic info /cmd_vel -v` 审计最终唯一 publisher。
4. 真实 YOLO 检测下的跟随闭环与超时取消。
5. Ollama `qwen3-embedding:0.6b` 的 1024 维语义召回。

## 6. Git 状态

改造前的干净基线提交是：

```text
08d48670 chore: checkpoint before further changes
```

当前改动（源码重写 + 测试重写 + 文档）仍在工作区，尚未提交。建议按主题拆分提交：

1. `refactor(car_agent): 以 Agent + 编排入口替换薄路由`（`src/agent/tools`、`supervisor`、`state`）；
2. `feat(car_agent): 接入执行状态未知协议并拆分跟随确认`（`workflows`）；
3. `test(car_agent): 重写主图与工作流测试`（`tests`）；
4. `docs(car_agent): 完成交接说明`（`docs`）。

提交前请确认 `agents/car_agent/src/agent/memory/nodes.py` 中删除 `router_steps` 的改动
与本轮改造属于同一主题。
