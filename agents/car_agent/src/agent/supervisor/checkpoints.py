"""在官方图执行入口拒绝不兼容 checkpoint，包括 Command 恢复。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import merge_configs
from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple
from langgraph.graph.state import CompiledStateGraph

SCHEMA_VERSION = 3
LEGACY_STATE_ERROR = "该会话使用旧版状态结构，无法安全恢复；请创建新会话"


class IncompatibleCheckpointError(ValueError):
    """旧状态不能运行、迁移或写回；调用者必须创建新会话。"""


class VersionedCarAgentGraph(CompiledStateGraph[Any, Any, Any, Any]):
    """只增加持久化状态预检，执行和中间件仍使用官方实现。"""

    @classmethod
    def from_compiled(
        cls, graph: CompiledStateGraph[Any, Any, Any, Any]
    ) -> VersionedCarAgentGraph:
        """保留官方编译结果的节点、Schema、存储和运行配置。"""
        guarded = cls(
            builder=graph.builder,
            schema_to_mapper=graph.schema_to_mapper,
            nodes=graph.nodes,
            channels=graph.channels,
            input_channels=graph.input_channels,
            output_channels=graph.output_channels,
            stream_channels=graph.stream_channels,
            stream_mode=graph.stream_mode,
            stream_eager=graph.stream_eager,
            interrupt_before_nodes=graph.interrupt_before_nodes,
            interrupt_after_nodes=graph.interrupt_after_nodes,
            step_timeout=graph.step_timeout,
            debug=graph.debug,
            checkpointer=graph.checkpointer,
            store=graph.store,
            cache=graph.cache,
            retry_policy=graph.retry_policy,
            cache_policy=graph.cache_policy,
            context_schema=graph.context_schema,
            config=graph.config,
            trigger_to_nodes=graph.trigger_to_nodes,
            node_error_handler_map=graph.node_error_handler_map,
            name=graph.name,
            stream_transformers=graph.stream_transformers,
        )
        # 官方编译时生成的序列化允许列表和 v2 输出映射也必须保留。
        guarded._serde_allowlist = graph._serde_allowlist
        guarded._output_mapper = graph._output_mapper
        guarded._state_mapper = graph._state_mapper
        return guarded

    def _checkpoint_source(
        self, config: RunnableConfig | None
    ) -> tuple[BaseCheckpointSaver | None, RunnableConfig]:
        merged = merge_configs(self.config, config)
        saver = merged.get("configurable", {}).get(
            "__pregel_checkpointer", self.checkpointer
        )
        validated_saver = (
            self._apply_checkpointer_allowlist(saver)
            if isinstance(saver, BaseCheckpointSaver)
            else None
        )
        return validated_saver, merged

    @staticmethod
    def _validate_checkpoint(saved: CheckpointTuple | None) -> None:
        if (
            saved is not None
            and saved.checkpoint["channel_values"].get("schema_version")
            != SCHEMA_VERSION
        ):
            raise IncompatibleCheckpointError(LEGACY_STATE_ERROR)

    def stream(
        self, input: Any, config: RunnableConfig | None = None, **kwargs: Any
    ) -> Iterator[Any]:
        """在新输入、空输入恢复和 Command 恢复之前校验存量状态。"""
        saver, merged = self._checkpoint_source(config)
        if saver is not None:
            self._validate_checkpoint(saver.get_tuple(merged))
        yield from super().stream(input, config, **kwargs)

    async def astream(
        self, input: Any, config: RunnableConfig | None = None, **kwargs: Any
    ) -> AsyncIterator[Any]:
        """异步入口同样在任何节点、模型或 Gateway 调用之前校验。"""
        saver, merged = self._checkpoint_source(config)
        if saver is not None:
            self._validate_checkpoint(await saver.aget_tuple(merged))
        async for chunk in super().astream(input, config, **kwargs):
            yield chunk
