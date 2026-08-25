"""评分维度目录 — 与 OpenJudge 评分器的映射关系。

LLM 型维度消耗 judge 模型 token；代码型维度确定性零成本。
分数统一归一化到 0-100 参与汇总。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Dimension:
    key: str
    label: str
    kind: str          # llm | code
    description: str
    scale: tuple[float, float]   # OpenJudge 原始分区间
    weight: float = 1.0


DIMENSIONS: dict[str, Dimension] = {
    "relevance": Dimension(
        "relevance", "相关性", "llm",
        "答复是否切题并给出有效内容（OpenJudge RelevanceGrader，1-5）",
        (1, 5), 1.2,
    ),
    "hallucination": Dimension(
        "hallucination", "幻觉控制", "llm",
        "是否存在无依据的编造信息（OpenJudge HallucinationGrader，1-5）",
        (1, 5), 1.4,
    ),
    "instruction_following": Dimension(
        "instruction_following", "指令遵循", "llm",
        "是否遵循用户指令与约束（OpenJudge InstructionFollowingGrader，1-5）",
        (1, 5), 1.2,
    ),
    "trajectory": Dimension(
        "trajectory", "轨迹质量", "llm",
        "整条执行路径的效率与正确性：绕路 / 冗余 / 死循环（TrajectoryComprehensiveGrader，0/1）",
        (0, 1),
    ),
    "action_loop": Dimension(
        "action_loop", "循环检测", "code",
        "工具动作重复度检测（ActionLoopDetectionGrader 算法，0/1，零成本）",
        (0, 1), 0.6,
    ),
}

# 基础维度：任何助手都默认勾选；轨迹类维度按会话数据自动追加
BASE_DIMENSION_KEYS = ("relevance", "hallucination", "instruction_following")
TRACE_DIMENSION_KEYS = ("trajectory", "action_loop")


def normalize(dim: Dimension, raw: float) -> float:
    lo, hi = dim.scale
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    value = max(lo, min(hi, value))
    if hi == lo:
        return 0.0
    # 幻觉维度的原始分语义：分越高越没幻觉 → 直接线性映射即可
    return round((value - lo) / (hi - lo) * 100, 1)


def root_cause_of(scores: dict[str, float], flags: dict) -> str:
    """按最低维度做轻量根因归类（Prompt / 工具 / 模型三类之一或整体良好）。"""
    if not scores:
        return ""
    flags = flags or {}
    if flags.get("loop_detected"):
        return "工具问题：存在重复动作循环"
    if flags.get("tool_error_count", 0) >= 2 and min(scores, key=scores.get) in {"trajectory"}:
        return "工具问题：多次调用失败影响执行路径"
    lowest = min(scores, key=scores.get)
    mapping = {
        "hallucination": "模型问题：答复存在无依据内容，建议强化 Prompt 的证据约束",
        "trajectory": "工具问题：执行路径绕路或低效，检查工具描述与选择提示",
        "action_loop": "工具问题：动作重复度高，检查技能与工具参数生成",
        "instruction_following": "Prompt 问题：未满足用户显式要求，检查系统提示与技能内联规则",
        "relevance": "需求理解偏差：答非所问，检查意图澄清与上下文压缩策略",
    }
    if scores[lowest] >= 70:
        return "整体良好"
    return mapping.get(lowest, "整体待优化")
