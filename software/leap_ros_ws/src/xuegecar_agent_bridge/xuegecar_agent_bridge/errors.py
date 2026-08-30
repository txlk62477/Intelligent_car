"""Gateway 对 HTTP seam 暴露的稳定领域错误。"""


class GatewayRejected(ValueError):
    """请求被确定性拒绝。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
