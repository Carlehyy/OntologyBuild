from app.models.user import User
from app.models.ontology import OntologyProject
from app.models.prompt import Prompt
from app.database import SessionLocal

NEW_CONTENT = """你是供应链领域本体工程专家。从文档中提取完整的供应链本体。

【实体类型】请识别以下所有类型的实体：
- 供应商(Supplier)：企业、供货商、合作伙伴
- 产品(Product)：成品、半成品、具体商品
- 物料(Material)：原材料、辅料、零部件
- 仓库(Warehouse)：仓储中心、配送中心、物流节点
- 采购订单(Document)：采购单、合同、订单等单据
- 类别(Category)：产品分类、供应商分类
- 工艺流程(Process)：生产工艺、采购流程、质检流程

每个实体必须填写 properties 字段（最多3个最重要的属性，如评级、产能、交货周期、容量等），不得省略。

【关系类型】尽可能多地提取：supply、IS-A、PART-OF、INSTANCE-OF、stores、processes、关联

【逻辑规则】提取IF-THEN形式业务规则。
每条规则必须填写 linked_entities，从已提取实体中选1-5个直接相关实体的name_cn，不得为空。

【动作】提取可执行业务动作。
每个动作必须填写 linked_entities（涉及实体）和 linked_logic_names（触发该动作的逻辑规则name_cn），均不得为空。

返回JSON，不要有多余文字：
{
  "entities": [
    {
      "name_cn": "中文名",
      "name_en": "EnglishName",
      "type": "Supplier|Product|Material|Warehouse|Document|Category|Process",
      "description": "描述",
      "properties": {"属性1": "值", "属性2": "值"},
      "confidence": 0.95
    }
  ],
  "relations": [
    {"source": "实体A的name_cn", "target": "实体B的name_cn", "type": "supply|IS-A|PART-OF|INSTANCE-OF|stores|processes|关联", "confidence": 0.85}
  ],
  "logic_rules": [
    {
      "name_cn": "规则名",
      "name_en": "RuleName",
      "formula": "IF 条件 THEN 结论",
      "description": "描述",
      "confidence": 0.9,
      "linked_entities": ["实体name_cn_1", "实体name_cn_2"]
    }
  ],
  "actions": [
    {
      "name_cn": "动作名",
      "name_en": "ActionName",
      "execution_rule": "触发条件及执行逻辑",
      "description": "描述",
      "confidence": 0.9,
      "linked_entities": ["实体name_cn_1", "实体name_cn_2"],
      "linked_logic_names": ["逻辑规则name_cn"]
    }
  ]
}"""

db = SessionLocal()
p = db.query(Prompt).filter(Prompt.name == "供应链本体提取").first()
if p:
    p.content = NEW_CONTENT
    db.commit()
    print("Updated:", p.name)
else:
    print("Not found!")
db.close()
