"""内置技能 seed — 按 name 不存在才插入，绝不覆盖用户的编辑。"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.capabilities.models import CapSkill

logger = logging.getLogger(__name__)

_ER_INSTRUCTIONS = """# ER 图绘制

当用户要求查看/绘制 ER 图（实体关系图）时，从**业务画布的对象模型**推导，在回答中输出一个 mermaid 代码块。

## 输出契约
1. 使用 ```mermaid 围栏代码块，首行 `erDiagram`。
2. 实体名用画布对象的 name（英文标识符），不要虚构画布中不存在的对象或属性。
3. 每个实体列出属性：`类型 属性名`，业务主键属性行尾加 `PK`。类型用 string/number/boolean/date/datetime。
4. 关系用画布对象的 relations 推导，基数映射：
   - one-to-one → `||--||`
   - one-to-many → `||--o{`
   - many-to-one → `}o--||`
   - many-to-many → `}o--o{`
5. 关系标签用关系的 display_name（如 `订单 ||--o{ 发票 : 开具`，标签放引号内可用中文）。

## 示例
```mermaid
erDiagram
    Order {
        string order_no PK
        number amount
    }
    Invoice {
        string invoice_no PK
    }
    Order ||--o{ Invoice : "开具"
```

画完后用一两句话说明图中关键关系；如果画布对象/关系信息不足，先告知缺什么并建议补充，不要硬画。
"""

_FLOW_INSTRUCTIONS = """# 业务流程图绘制

当用户要求查看/绘制业务流程图时，从**业务画布的场景模型与行为模型**推导，在回答中输出一个 mermaid 代码块。

## 输出契约
1. 使用 ```mermaid 围栏代码块，首行 `flowchart TD`。
2. 以某个场景的 steps 为主线；每个步骤一个节点，节点 id 用英文（S1、S2…），节点文字用中文描述。
3. 有明确执行主体时，用 `subgraph 主体名 ... end` 把该主体执行的节点分入泳道。
4. 判断/审批点用菱形节点 `{是否通过?}`，分支边标注 `-->|通过|` / `-->|拒绝|`。
5. 步骤引用的行为用画布行为的 display_name，不要虚构画布中不存在的行为或主体。

## 示例
```mermaid
flowchart TD
    subgraph 业务员
        S1[创建订单]
    end
    subgraph 财务
        S2[回款核销]
        S3{金额>10万?}
    end
    S1 --> S2 --> S3
    S3 -->|是| S4[主管审批]
    S3 -->|否| S5[核销完成]
```

画完后简述流程要点；场景信息不足时先建议用户补充场景模型。
"""

BUILTIN_SKILLS = [
    {"name": "er_diagram", "display_name": "ER 图绘制",
     "description": "用户想看实体关系图/ER 图时使用：从画布对象模型推导，输出 mermaid erDiagram。",
     "instructions": _ER_INSTRUCTIONS, "scopes": ["exploration"]},
    {"name": "business_flowchart", "display_name": "业务流程图绘制",
     "description": "用户想看业务流程图/泳道图时使用：从画布场景与行为模型推导，输出 mermaid flowchart。",
     "instructions": _FLOW_INSTRUCTIONS, "scopes": ["exploration"]},
]


def seed_builtin_skills(db: Session) -> int:
    """幂等 seed：只补缺失的内置技能，返回新增数。"""
    created = 0
    for spec in BUILTIN_SKILLS:
        if db.query(CapSkill).filter(CapSkill.name == spec["name"]).first():
            continue
        db.add(CapSkill(**spec, builtin=True, enabled=True))
        created += 1
    if created:
        db.commit()
        logger.info("seed 内置技能 %d 个", created)
    return created
