"""提交结果未知时的幂等恢复协议。

有副作用的提交超时或通信失败时，绝不能推断为“未执行”，也不能换一个新的
operation_id 盲目重提。唯一允许的动作是：用同一个 operation_id 查询真实状态，
再按查询结论决定继续轮询、判定未提交，或报告 execution_unknown。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Collection, Mapping
from typing import Any, Literal, TypedDict

from agent.common.robot_gateway import RobotGatewayError

#: 提交成功但状态无法确认时的终态。
UNKNOWN_STATUS = "execution_unknown"
#: 提交未被确认且 Gateway 没有对应记录时的错误码。
NOT_FOUND_CODE = "SUBMIT_NOT_FOUND"

SubmissionResolution = Literal["recovered", "not_found", "unknown"]


class SubmissionRecovery(TypedDict):
    """一次提交异常后的查询结论。"""

    resolution: SubmissionResolution
    record: dict[str, Any] | None
    error_code: str
    error: str


def recover_submission(
    *,
    getter: Callable[[str], dict[str, Any]],
    operation_id: str,
    error: RobotGatewayError,
) -> SubmissionRecovery:
    """用同一个 operation_id 查询提交结果，禁止生成新 ID 重提。"""
    try:
        record = getter(operation_id)
    except RobotGatewayError as query_error:
        if query_error.code == "NOT_FOUND":
            return SubmissionRecovery(
                resolution="not_found",
                record=None,
                error_code=NOT_FOUND_CODE,
                error=f"提交未得到确认，且 Gateway 没有该操作记录：{query_error}",
            )
        return SubmissionRecovery(
            resolution="unknown",
            record=None,
            error_code=str(error.code or "UNAVAILABLE"),
            error=f"提交结果未知，按同一操作编号查询也失败：{query_error}",
        )
    return SubmissionRecovery(
        resolution="recovered",
        record=dict(record),
        error_code="",
        error="",
    )


def not_submitted_record(
    *, payload: dict[str, Any], error_code: str, error: str
) -> dict[str, Any]:
    """构造“确认未提交”的失败记录。"""
    return {**payload, "status": "FAILED", "error_code": error_code, "error": error}


def is_pending(record: Mapping[str, Any], terminal: Collection[str]) -> bool:
    """记录是否仍需继续轮询。"""
    status = str(record.get("status") or "")
    return status not in terminal and status != UNKNOWN_STATUS


async def best_effort(action: Callable[..., Any], *args: Any) -> None:
    """尽力执行一次清理调用；清理失败不覆盖原始结论。"""
    try:
        await asyncio.to_thread(action, *args)
    except RobotGatewayError:
        pass


def unknown_record(*, operation_id: str, error_code: str, error: str) -> dict[str, Any]:
    """构造“执行状态未知”的终态记录。"""
    return {
        "operation_id": operation_id,
        "status": UNKNOWN_STATUS,
        "error_code": error_code,
        "error": error,
    }


__all__ = [
    "NOT_FOUND_CODE",
    "UNKNOWN_STATUS",
    "SubmissionRecovery",
    "best_effort",
    "is_pending",
    "not_submitted_record",
    "recover_submission",
    "unknown_record",
]
