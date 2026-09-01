"""地图作用域位置记忆的深模块。"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Literal

from langgraph.store.base import BaseStore
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent.memory.models import utc_now


class MapPose(BaseModel):
    """二维 map 坐标系目标位姿。"""

    model_config = ConfigDict(extra="forbid")

    x: float
    y: float
    yaw: float
    frame_id: Literal["map"] = "map"

    @field_validator("x", "y", "yaw")
    @classmethod
    def finite(cls, value: float) -> float:
        """拒绝无法安全传给 Nav2 的非有限坐标。"""
        if not math.isfinite(value):
            raise ValueError("地图坐标必须是有限数值")
        return float(value)


class MapLocation(BaseModel):
    """官方 Store 中可由 Nav2 执行的位置资产。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["map_location"] = "map_location"
    schema_version: Literal[1] = 1
    label: str
    aliases: list[str] = Field(default_factory=list)
    pose: MapPose
    map_id: str
    map_name: str = ""
    robot_id: str
    needs_review: bool = False
    success_count: int = 0
    failure_count: int = 0
    last_used_at: str | None = None
    last_result: str | None = None
    created_by: str
    updated_by: str
    source_thread_id: str
    source_run_id: str
    created_at: str
    updated_at: str

    @field_validator("label", "map_id", "robot_id", "created_by", "updated_by")
    @classmethod
    def nonempty(cls, value: str) -> str:
        """清理位置资产的必填文本。"""
        value = value.strip()
        if not value:
            raise ValueError("位置必填字段不能为空")
        return value[:256]

    @field_validator("aliases")
    @classmethod
    def clean_aliases(cls, values: list[str]) -> list[str]:
        """清理位置别名并保持输入顺序去重。"""
        return list(
            dict.fromkeys(item.strip()[:100] for item in values if item.strip())
        )


class LocationConflict(ValueError):
    """同一地图中的名称或别名已被其他位置占用。"""


class LocationStore:
    """隐藏 namespace、别名唯一性、语义召回和使用统计。"""

    def __init__(self, store: BaseStore, *, robot_id: str, map_id: str) -> None:
        """绑定单个机器人和单张地图的 Store namespace。"""
        if not robot_id.strip() or not map_id.strip():
            raise ValueError("robot_id 和 map_id 不能为空")
        self._store = store
        self._robot_id = robot_id.strip()
        self._map_id = map_id.strip()
        self._namespace = (
            "robots",
            self._robot_id,
            "maps",
            self._map_id,
            "locations",
        )

    @property
    def namespace(self) -> tuple[str, ...]:
        """返回 Studio 中可见的位置 namespace。"""
        return self._namespace

    async def list(self) -> list[MapLocation]:
        """列出当前地图中的全部合法位置。"""
        items = await self._store.asearch(self._namespace, limit=200)
        locations: list[MapLocation] = []
        for item in items:
            try:
                location = MapLocation.model_validate(item.value)
            except Exception:
                continue
            if location.map_id == self._map_id and location.robot_id == self._robot_id:
                locations.append(location)
        return locations

    async def resolve(self, query: str, *, limit: int = 5) -> list[MapLocation]:
        """按标准名称、别名和语义相似依次解析当前地图位置。"""
        normalized = normalize_location_name(query)
        locations = await self.list()
        exact = [
            location
            for location in locations
            if normalized
            in {
                normalize_location_name(location.label),
                *(normalize_location_name(alias) for alias in location.aliases),
            }
        ]
        if exact:
            return exact[:limit]
        try:
            items = await self._store.asearch(
                self._namespace,
                query=query,
                limit=limit,
            )
        except Exception:
            return []
        matches: list[MapLocation] = []
        for item in items:
            score = getattr(item, "score", None)
            if score is not None and float(score) < 0.75:
                continue
            try:
                location = MapLocation.model_validate(item.value)
            except Exception:
                continue
            if location.map_id == self._map_id and location.robot_id == self._robot_id:
                matches.append(location)
        return matches

    async def get(self, label: str) -> MapLocation | None:
        """按稳定 key 读取一个位置。"""
        item = await self._store.aget(self._namespace, location_key(label))
        if item is None:
            return None
        location = MapLocation.model_validate(item.value)
        if location.map_id != self._map_id or location.robot_id != self._robot_id:
            return None
        return location

    async def save(
        self,
        *,
        label: str,
        aliases: list[str],
        pose: MapPose,
        map_name: str,
        user_id: str,
        thread_id: str,
        run_id: str,
    ) -> MapLocation:
        """新增或更新位置，同时强制同图别名唯一。"""
        label = label.strip()
        aliases = list(
            dict.fromkeys(alias.strip() for alias in aliases if alias.strip())
        )
        key = location_key(label)
        existing = await self.get(label)
        claimed = {
            normalize_location_name(label),
            *(normalize_location_name(alias) for alias in aliases),
        }
        for location in await self.list():
            if location_key(location.label) == key:
                continue
            names = {
                normalize_location_name(location.label),
                *(normalize_location_name(alias) for alias in location.aliases),
            }
            overlap = sorted(claimed & names)
            if overlap:
                raise LocationConflict(
                    f"名称或别名已被位置 {location.label!r} 使用：{overlap[0]}"
                )
        now = utc_now()
        location = MapLocation(
            label=label,
            aliases=aliases,
            pose=pose,
            map_id=self._map_id,
            map_name=map_name.strip()[:256],
            robot_id=self._robot_id,
            needs_review=False if existing is None else existing.needs_review,
            success_count=0 if existing is None else existing.success_count,
            failure_count=0 if existing is None else existing.failure_count,
            last_used_at=None if existing is None else existing.last_used_at,
            last_result=None if existing is None else existing.last_result,
            created_by=user_id if existing is None else existing.created_by,
            updated_by=user_id,
            source_thread_id=thread_id,
            source_run_id=run_id,
            created_at=now if existing is None else existing.created_at,
            updated_at=now,
        )
        await self._store.aput(
            self._namespace,
            key,
            location.model_dump(mode="json"),
            index=["label", "aliases"],
        )
        return location

    async def delete(self, label: str) -> bool:
        """删除当前地图的精确位置。"""
        existing = await self.get(label)
        if existing is None:
            return False
        await self._store.adelete(self._namespace, location_key(label))
        return True

    async def record_result(self, location: MapLocation, status: str) -> MapLocation:
        """更新使用统计，永不修改目标 pose。"""
        succeeded = status == "SUCCEEDED"
        failures = location.failure_count + (0 if succeeded else 1)
        updated = location.model_copy(
            update={
                "success_count": location.success_count + (1 if succeeded else 0),
                "failure_count": failures,
                "last_used_at": utc_now(),
                "last_result": status,
                "needs_review": location.needs_review or failures >= 3,
                "updated_at": utc_now(),
            }
        )
        await self._store.aput(
            self._namespace,
            location_key(location.label),
            updated.model_dump(mode="json"),
            index=["label", "aliases"],
        )
        return updated


def normalize_location_name(value: str) -> str:
    """生成仅用于比较的 Unicode 位置名称。"""
    return re.sub(r"[\s，。！？、,.!?:：;；_-]+", "", value.strip().lower())


def location_key(label: str) -> str:
    """生成稳定且适合 Store 的位置 key。"""
    normalized = normalize_location_name(label)
    ascii_key = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return ascii_key[:80] or hashlib.sha256(normalized.encode()).hexdigest()[:24]
