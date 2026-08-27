# 智能小车 Agent 架构构想


## 目标

在现有视觉、SLAM 建图、Nav2 导航和底盘控制能力之上，引入基于 LangGraph 的混合 Agent，使用户能够通过自然语言查询小车状态、理解视觉场景并下达复合任务。

基本分工：

- LangGraph 负责理解意图、规划任务、调用能力和组织回复。
- ROS2 负责传感器接入、建图、导航、避障和实时运动控制。
- micro-ROS Agent 负责 ROS2 与底层单片机之间的 DDS-XRCE 通信。
- 安全规则负责限速、超时停车、急停和人工接管，不交给大模型判断。

## 总体结构

```text
用户界面
   │ LangGraph SDK / REST
   ▼
官方 LangGraph Agent Server
   │
   ▼
Supervisor
   ├── 通用问答 Agent
   ├── 视觉理解 Agent
   ├── 任务规划 Agent（后期）
   │
   ├── 状态查询 Tool
   ├── 导航 Workflow
   ├── 定时移动 Workflow
   ├── 建图 Workflow
   ├── 巡逻 Workflow
   └── 急停 Tool
   │
   ▼
Robot Gateway
   │ ROS2 Topic / Service / Action
   ▼
ROS2 / micro-ROS / 小车
```

LangGraph 项目应独立于 ROS2 工作空间，不放入 `software/leap_ros_ws/src`。两者分别构建、运行，并通过 Robot Gateway 的小型结构化接口连接。

## Agent、Workflow 和 Tool 的选择

选择原则：

- 执行顺序固定、安全敏感、结果必须确定的能力使用 Workflow。
- 需要理解开放问题、推理或自主选择工具的能力使用 Agent。
- 单纯读取状态或执行单个确定动作的能力使用 Tool。

| 用户需求 | 推荐实现 |
| --- | --- |
| “前进半米” | 固定移动 Workflow |
| “去客厅” | Agent 解析地点 + 固定导航 Workflow |
| “开始建图” | 固定建图 Workflow |
| “保存当前地图” | 固定 Workflow + 人工确认 |
| “小车现在在哪里” | 状态查询 Tool |
| “前面有什么” | 视觉理解 Agent |
| “前面能不能通过” | 视觉 Agent + 雷达、地图和安全规则 |
| “去门口看看有没有人” | 任务 Workflow，组合导航与视觉 Agent |
| “巡逻一圈，有异常告诉我” | 巡逻 Workflow + 视觉 Agent |
| “为什么导航失败” | 后期增加诊断 Agent |
| 普通项目与操作问题 | 通用问答 Agent |

## 初期专业 Agent

初期不宜把每项功能都拆成 Agent，建议只保留：

1. **视觉理解 Agent**：负责图像描述、目标识别、场景问答和巡检结果。
2. **通用问答 Agent**：负责项目说明、操作指导和任务解释。

Supervisor 负责识别意图、选择 Agent 或 Workflow，并汇总结果。任务规划、故障诊断和地图语义可以在确有需求后再拆分。

## 固定 Workflow

### 移动控制

```text
解析结构化参数
→ 检查速度、方向和持续时间
→ 必要时人工确认
→ 检查小车状态
→ 执行移动
→ 超时自动停车
→ 返回执行结果
```

大模型只能提出方向、目标速度和持续时间，不能自行循环发布 `/cmd_vel`。所有 ROS2 写操作由 Robot Gateway 执行；急停不经过大模型，也不需要确认。

### 视觉询问

```text
获取当前图像
→ 视觉模型理解
→ 必要时结合地图和小车位置
→ 返回观察结果
```

视觉 Agent 负责描述和判断，但不直接控制电机。例如视觉 Agent 可以报告“前方疑似有纸箱”，安全规则则应在不确定时禁止继续前进。

### 复合任务示例

用户指令：

> 去门口看看有没有人，然后回来。

可能的执行过程：

```text
Supervisor
→ 查询“门口”的语义位置
→ 形成任务计划
→ 人工确认
→ 导航 Workflow 前往门口
→ 视觉 Agent 判断是否有人
→ 导航 Workflow 返回起点
→ Supervisor 汇总结果
```

导航和避障始终由 Nav2 与 ROS2 执行，视觉理解由专业 Agent 完成，Supervisor 只负责任务编排。

## Robot Gateway 的职责

Robot Gateway 是 LangGraph 与 ROS2 之间的适配器：

- Graph 通过结构化接口查询状态、导航、移动和停车。
- 网关订阅 `/odom`、电量等 ROS2 话题并维护状态快照。
- 网关调用 Nav2 Action，或向经过安全仲裁的速度话题发布命令。
- 网关负责限速、最长执行时间、掉线停车、任务取消和错误归一化。
- LangGraph 不导入 `rclpy`，不持有 Publisher，也不运行 ROS Executor。

建议将不同控制来源分开，再统一仲裁：

```text
键盘  → /cmd_vel_keyboard ─┐
Nav2  → /cmd_vel_nav ──────┼→ 安全仲裁 → /cmd_vel → 小车
Agent → /cmd_vel_agent ────┘
```

键盘急停和人工接管的优先级应始终高于 Agent。

## LangGraph 能力规划

- **Supervisor**：动态识别意图并路由到专业 Agent、Tool 或 Workflow。
- **ReAct**：只用于开放式理解、查询和任务规划，不进入实时控制循环。
- **结构化交接**：Agent 之间只传递任务、事实、结果和错误，不传递无限增长的完整上下文。
- **人工确认**：移动、导航、覆盖地图等动作可通过 Interrupt 暂停并等待确认。
- **中断恢复**：依赖 Checkpoint 保存任务状态，恢复时避免重复执行非幂等动作。
- **跨会话记忆**：保存用户偏好、命名地点和任务历史，不保存 ROS2 运行时对象。
- **执行预算**：限制模型调用次数、工具调用次数、任务时长、移动距离和费用。
- **上下文管理**：Supervisor 维护主会话，专业 Agent 只接收完成任务所需的最小上下文。
- **可观测性**：使用 LangGraph Studio 查看图状态，使用 LangSmith 跟踪路由、工具调用、耗时和失败。

## 演进路线

1. **只读阶段**：查询位置、电量、连接状态，回答“前面有什么”。
2. **受控执行阶段**：在人工确认后执行短距离移动和单点导航。
3. **组合任务阶段**：实现“导航—观察—返回”等有限复合任务。
4. **环境记忆阶段**：维护命名地点、历史观察、用户偏好和任务记录。
5. **有限自主阶段**：实现定时巡逻和异常报告，同时保留急停、预算和人工接管。

适合作为首个闭环验证的场景：

> 查询小车状态并拍摄前方图像；经过人工确认后，导航到指定地图点并停车。

该场景可以同时验证对话、视觉、状态读取、导航、安全确认、中断恢复和执行反馈，复杂度相对可控。

## 暂不确定的事项

当前阶段不急于确定：

- 是否拆分多个独立 Agent，还是先用单 Agent 加动态技能。
- 使用本地模型、云端模型或混合模型。
- 长期记忆的具体数据库和向量存储。
- Robot Gateway 最终采用 HTTP、WebSocket 或其他进程间通信方式。
- 是否引入多模态实时视频，以及图像采样与成本策略。
- 是否需要多机器人协作。

应先从一个可验证的真实场景出发，再根据复杂度决定是否增加新的 seam 或专业 Agent。

## 参考文档

- [LangGraph：Workflows and agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents)
- [LangChain：Multi-agent](https://docs.langchain.com/oss/python/langchain/multi-agent/index)
- [LangChain：Subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)
- [LangGraph：Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph：Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
