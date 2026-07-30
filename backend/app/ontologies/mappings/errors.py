"""Stable Mapping domain exceptions shared by facade and extracted services."""
from __future__ import annotations


class MappingSourceError(ValueError):
    """映射绑定的数据版本不可读取、为空或未满足治理闸门。"""


class MappingApplyError(RuntimeError):
    """映射写入或正规本体投影失败；调用方不得把本次运行视为 applied。"""


class MappingReleaseScopeError(MappingApplyError):
    """Mutable runtime mapping definitions do not match the current release."""


class MappingSentinelDispatchError(MappingApplyError):
    """Projection committed, but its durable Sentinel cascade did not finish."""

    def __init__(self, ontology_id: str, dispatch: dict):
        self.ontology_id = ontology_id
        self.dispatch = dispatch
        super().__init__(
            "关系型/Formal 投影已提交，但 Sentinel 下游级联失败；"
            "已阻断本次构建确认，durable CDC outbox 将保留失败/重试证据："
            f"{dispatch.get('errors')}"
        )
