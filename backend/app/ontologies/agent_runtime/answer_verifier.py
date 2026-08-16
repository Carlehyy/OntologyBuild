"""
确定性结论校验 (Answer Verifier) — 借鉴 DataFoundry 的 ``result-verifier.ts``。

把「先查证后回答」从 prompt 道德约束变成代码强制：回合终答前，
把回答文本中的数值断言与回合内工具结果的数值集合做程序化比对；
无法对应到任何工具结果的数字视为「未验证」，先给模型一次修正机会，
仍不通过则在答案中显式标注，而不是静默放行。

原则（fail-open 但显式标注）：
- 校验失败不摧毁回答（与治理一致：标注未验证，而非拦截）；
- 无工具步骤或无数字的回答直接通过；
- 结果集合自动加入 x*100 / x/100 变体，兼容「35% ↔ 0.35」等表达；
- 年份与用户自己给出的数字不参与校验（来自用户/上下文，无需工具背书）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# 数字抽取：覆盖 整数 / 小数 / 千分位 / 负号。年份先整体剥离（否则 "2026"
# 会被切成 202+6），版本号等含 4 位数字的字符串也会被剥离年份部分——可接受的
# 误伤，宁可少校验也不把年份当数据断言。
_NUMBER_RE = re.compile(r"-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-?\d+\.\d+")
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


@dataclass
class VerificationResult:
    """一次校验的结构化结果（可序列化进 answer 事件与审计）。"""
    answer_numbers: list[float] = field(default_factory=list)
    unverified: list[float] = field(default_factory=list)
    matched: list[float] = field(default_factory=list)
    retried: bool = False

    @property
    def passed(self) -> bool:
        return not self.unverified

    def summary(self) -> dict:
        return {
            "passed": self.passed,
            "answerNumberCount": len(self.answer_numbers),
            "unverified": self.unverified,
            "retried": self.retried,
        }


def _numbers_in_text(text: str) -> list[float]:
    """抽取文本中的数值（千分位去掉逗号）；年份整体剥离后不再参与。"""
    cleaned = _YEAR_RE.sub(" ", text or "")
    out: list[float] = []
    for raw in _NUMBER_RE.findall(cleaned):
        out.append(float(raw.replace(",", "")))
    return out


def _numbers_in_value(value: object) -> list[float]:
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, str):
        return _numbers_in_text(value)
    if isinstance(value, dict):
        numbers: list[float] = []
        for child in value.values():
            numbers.extend(_numbers_in_value(child))
        return numbers
    if isinstance(value, (list, tuple)):
        numbers: list[float] = []
        for child in value:
            numbers.extend(_numbers_in_value(child))
        return numbers
    return []


def _with_ratio_variants(numbers: Iterable[float]) -> set[float]:
    allowed: set[float] = set()
    for number in numbers:
        allowed.add(number)
        allowed.add(round(number * 100, 2))
        if number != 0:
            allowed.add(round(number / 100, 4))
    return allowed


def result_number_universe(steps: Iterable[dict]) -> set[float]:
    """回合内所有工具结果产出的数值集合（含百分比变体）。"""
    numbers: list[float] = []
    for step in steps:
        result = step.get("result")
        if not isinstance(result, dict):
            continue
        if result.get("error") or result.get("_truncated"):
            # 出错/截断的结果不能作为数值背书来源
            continue
        numbers.extend(_numbers_in_value(result))
    return _with_ratio_variants(numbers)


def answer_numbers(answer: str) -> list[float]:
    """回答文本中的数值断言（保留出现顺序与重复）。"""
    return _numbers_in_text(answer)


def verify_answer(answer: str, steps: Iterable[dict]) -> VerificationResult:
    universe = result_number_universe(steps)
    numbers = answer_numbers(answer)
    result = VerificationResult(answer_numbers=numbers)
    for number in numbers:
        (result.matched if number in universe else result.unverified).append(number)
    return result


def correction_prompt(unverified: Iterable[float]) -> str:
    values = "、".join(f"{value:g}" for value in unverified)
    return (
        f"# 结论校验失败\n"
        f"你刚才回答中的以下数字未能对应到任何工具返回结果，"
        f"不得编造数据：{values}。\n"
        f"请重新作答：只保留能对应到工具结果的数字；若确实缺少数据，"
        f"明确说明缺少什么并删除无法佐证的数字。不要新增其它工具调用。"
    )


def unverified_notice(unverified: Iterable[float]) -> str:
    values = "、".join(f"{value:g}" for value in unverified)
    return (
        f"\n\n> ⚠️ 结论校验：以下数字未能与工具返回结果对应，请自行核对后再使用：{values}。"
    )
