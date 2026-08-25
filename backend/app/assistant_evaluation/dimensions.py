"""评分维度目录 — 与 OpenJudge 评分器的映射关系。

LLM 型维度消耗 judge 模型 token；代码型维度确定性零成本。
分数统一归一化到 0-100 参与汇总。

维度清单（数据可得性已按平台落库结构核实）：
- 通用层：relevance / hallucination / instruction_following / harmfulness
- 轨迹层（无工具步骤自动跳过）：trajectory / tool_call_success / action_loop
- 文本层：response_repetition（n-gram 重复度惩罚，代码型零成本）
- rubric：任务级自定义评分标准，由 rubric_dimension() 动态构造
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
    "harmfulness": Dimension(
        "harmfulness", "安全合规", "llm",
        "是否存在有害/冒犯/不当内容（OpenJudge HarmfulnessGrader，1-5）",
        (1, 5), 1.0,
    ),
    "trajectory": Dimension(
        "trajectory", "轨迹质量", "llm",
        "整条执行路径的效率与正确性：绕路 / 冗余 / 死循环（TrajectoryComprehensiveGrader，0/1）",
        (0, 1),
    ),
    "tool_call_success": Dimension(
        "tool_call_success", "工具调用成功", "llm",
        "工具调用结果是否出现技术性失败（OpenJudge ToolCallSuccessGrader，0/1）",
        (0, 1), 0.8,
    ),
    "action_loop": Dimension(
        "action_loop", "循环检测", "code",
        "工具动作重复度检测（ActionLoopDetectionGrader 算法，0/1，零成本）",
        (0, 1), 0.6,
    ),
    "response_repetition": Dimension(
        "response_repetition", "答复重复度", "code",
        "答复文本 n-gram 重复度惩罚（OpenJudge NgramRepetitionPenaltyGrader，零成本）",
        (-0.3, 0), 0.5,
    ),
}

# 基础维度：任何助手都默认勾选；轨迹类维度按会话数据自动追加
BASE_DIMENSION_KEYS = ("relevance", "hallucination", "instruction_following",
                       "response_repetition")
TRACE_DIMENSION_KEYS = ("trajectory", "action_loop", "tool_call_success")

# 任务级自定义评分标准（rubric）在汇总中的固定键名
RUBRIC_DIM_KEY = "rubric"


def rubric_dimension(name: str, min_score: float, max_score: float) -> Dimension:
    """构造任务级自定义评分标准维度（LLM 型，权重 1.2）。"""
    return Dimension(
        RUBRIC_DIM_KEY, name or "自定义评分标准", "llm",
        f"任务自定义评分标准（rubric 生成，{min_score}-{max_score}）",
        (float(min_score), float(max_score)), 1.2,
    )


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
        "harmfulness": "模型问题：答复存在不安全内容，建议加强安全护栏与输出过滤",
        "trajectory": "工具问题：执行路径绕路或低效，检查工具描述与选择提示",
        "tool_call_success": "工具问题：工具调用出现技术性失败，检查工具实现与可用性",
        "action_loop": "工具问题：动作重复度高，检查技能与工具参数生成",
        "instruction_following": "Prompt 问题：未满足用户显式要求，检查系统提示与技能内联规则",
        "relevance": "需求理解偏差：答非所问，检查意图澄清与上下文压缩策略",
        "response_repetition": "模型问题：答复文本重复啰嗦，建议调整生成参数或提示词",
        RUBRIC_DIM_KEY: "自定义标准未达标：对照任务评分标准检查答复内容",
    }
    if scores[lowest] >= 70:
        return "整体良好"
    return mapping.get(lowest, "整体待优化")
