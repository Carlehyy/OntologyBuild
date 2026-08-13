"""业务探索内置技能。

这些技能只服务业务探索运行时，随代码发布，不依赖平台级能力注册中心或数据库。
保留 ``use_skill`` 渐进披露协议，避免把完整指令常驻系统提示。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExplorationSkill:
    name: str
    display_name: str
    description: str
    instructions: str


_ER_INSTRUCTIONS = """# ER 图绘制

ER 图必须是**权威业务画布的确定性投影**，不能由模型手写 Mermaid。

## 执行契约
1. 先核对画布中的对象、主键、属性、关系端点和基数；不得虚构缺失内容。
2. 若关系端点或基数不完整，先用画布/澄清工具补齐已确认事实，或登记堵门问题。
3. 信息足够后调用 `show_diagram`，参数为 `{"kind": "er"}`。工具会统一完成
   Mermaid（`erDiagram`）生成、引用校验、历史持久化和前端渲染。
4. 绝不在正文中自行输出 Mermaid 代码块；也不要在 `show_diagram` 报错后绕过质量门手写图。
5. 工具成功后，用一两句话说明关键关系，并明确请用户核对画布事实是否符合实际。
"""

_FLOW_INSTRUCTIONS = """# 业务流程图绘制

业务流程图必须是**场景或流程、行为和主体画布的确定性投影**，不能由模型手写 Mermaid。

## 执行契约
1. 先核对目标场景或流程的参与主体、步骤、行为引用、分支条件/去向和预期结果。
2. 场景不完整时，先补齐已确认事实或登记堵门问题，不得用自然语言猜测分支。
3. 信息足够后调用 `show_diagram`：单场景/单流程使用
   `{"kind": "flow", "target": "<场景 name/display_name 或流程 name/display_name>"}`；只有一个场景/流程时可省略 target。
4. 工具会统一生成 `flowchart TD` 泳道/分支、执行引用校验、持久化并交给前端渲染。
5. 绝不在正文中自行输出 Mermaid 代码块；也不要在工具报错后绕过质量门手写图。
6. 工具成功后简述主路径和异常分支，并请用户核对是否符合实际执行顺序。
"""


_SKILLS = (
    ExplorationSkill(
        name="er_diagram",
        display_name="ER 图绘制",
        description="用户想看实体关系图/ER 图时使用：核对画布后调用 show_diagram 生成确定性投影。",
        instructions=_ER_INSTRUCTIONS,
    ),
    ExplorationSkill(
        name="business_flowchart",
        display_name="业务流程图绘制",
        description="用户想看业务流程图/泳道图时使用：核对场景后调用 show_diagram 生成确定性投影。",
        instructions=_FLOW_INSTRUCTIONS,
    ),
)


def exploration_skills() -> dict[str, ExplorationSkill]:
    """返回业务探索可用技能的新字典，防止回合内修改全局注册表。"""
    return {skill.name: skill for skill in _SKILLS}
