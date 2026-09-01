"""长期记忆 JSON 文档和结构化提取模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProfileIdentity(BaseModel):
    """可跨会话使用的用户身份偏好。"""

    model_config = ConfigDict(extra="forbid")

    preferred_name: str | None = None


class ProfilePreferences(BaseModel):
    """不影响车辆安全约束的稳定交互偏好。"""

    model_config = ConfigDict(extra="forbid")

    language: str | None = None
    distance_unit: str | None = None
    response_style: str | None = None


class StableFact(BaseModel):
    """带来源的稳定用户事实。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    content: str
    source_thread_id: str
    source_run_id: str
    updated_at: str
    confidence: float = Field(ge=0.0, le=1.0)


class UserProfile(BaseModel):
    """Store 中 profile/current 的完整快照。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["user_profile"] = "user_profile"
    schema_version: Literal[1] = 1
    user_id: str
    identity: ProfileIdentity = Field(default_factory=ProfileIdentity)
    preferences: ProfilePreferences = Field(default_factory=ProfilePreferences)
    facts: list[StableFact] = Field(default_factory=list)
    updated_at: str


class FactUpsert(BaseModel):
    """模型请求新增或替换的稳定事实。"""

    model_config = ConfigDict(extra="forbid")

    key: str
    content: str
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)

    @field_validator("key", "content")
    @classmethod
    def validate_text(cls, value: str) -> str:
        """清理事实键和值并限制单项长度。"""
        value = value.strip()
        if not value:
            raise ValueError("稳定事实不能为空")
        return value[:500]


class MemoryExtraction(BaseModel):
    """每轮对话的受限结构化记忆增量。"""

    model_config = ConfigDict(extra="forbid")

    episode_summary: str
    important_facts: list[str] = Field(default_factory=list)
    preferred_name: str | None = None
    language: str | None = None
    distance_unit: str | None = None
    response_style: str | None = None
    fact_upserts: list[FactUpsert] = Field(default_factory=list)
    fact_removals: list[str] = Field(default_factory=list)

    @field_validator("episode_summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        """拒绝空摘要并限制长期记忆大小。"""
        value = value.strip()
        if not value:
            raise ValueError("对话摘要不能为空")
        return value[:1200]

    @field_validator("important_facts", "fact_removals")
    @classmethod
    def clean_string_list(cls, values: list[str]) -> list[str]:
        """清理、去重并限制字符串列表。"""
        return list(
            dict.fromkeys(item.strip()[:500] for item in values if item.strip())
        )


class EpisodeMemory(BaseModel):
    """Store 中一轮对话的压缩情景记忆。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["conversation_episode"] = "conversation_episode"
    schema_version: Literal[1] = 1
    summary: str
    important_facts: list[str] = Field(default_factory=list)
    user_id: str
    robot_id: str
    thread_id: str
    run_id: str
    created_at: str


def utc_now() -> str:
    """返回适合写入 JSON 的 UTC 时间。"""
    return datetime.now(UTC).isoformat()
