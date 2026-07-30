"""Stable Action execution error types shared by runtime layers."""
from __future__ import annotations


class RuleExecutionError(Exception):
    """单条规则执行失败——携带规则名，供失败日志展示。"""
    def __init__(self, rule_name: str, message: str):
        super().__init__(f"规则「{rule_name}」执行失败: {message}")
