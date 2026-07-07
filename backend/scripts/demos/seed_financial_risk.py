"""
金融风控领域种子数据

领域：企业关联风险、担保链、实控人识别
包含：
- 企业实体 (20+)
- 个人实体 (10+)
- 关系 (30+)
- 属性Schema (15+)
- 逻辑规则 (5)
- 动作 (3)
- 词表 (20+)
"""
import sys
sys.path.insert(0, "/mnt/agents/nano-ontoprompt/backend")

import uuid
from datetime import datetime, timezone
from app.database import SessionLocal, Base, engine
from app.models.ontology import OntologyProject
from app.models.entity import Entity
from app.models.relation import Relation
from app.models.logic import LogicRule
from app.models.action import Action
from app.models.attribute_schema import AttributeSchema, VocabularyEntry
from app.models.user import User
from app.services.auth_service import hash_password

def seed():
    db = SessionLocal()
    try:
        # 确保admin存在
        admin = db.query(User).filter(User.role == "admin").first()
        if not admin:
            admin = User(
                id=str(uuid.uuid4()),
                username="admin",
                email="admin@ontoprompt.local",
                password_hash=hash_password("admin123"),
                role="admin",
            )
            db.add(admin)
            db.commit()

        # ── 创建本体 ──
        onto_id = str(uuid.uuid4())
        ontology = OntologyProject(
            id=onto_id,
            name="金融风控知识图谱",
            domain="金融风控",
            description="企业关联风险、担保链、实控人识别本体模型",
            version="v1.0.0",
            status="published",
            build_mode="simple_llm",
            created_by=admin.id,
        )
        db.add(ontology)

        # ── 属性Schema ──
        schemas = [
            ("registered_capital", "注册资本", "number", {"required": False, "min": 0, "unit": "万元"}, ["企业"]),
            ("establishment_date", "成立日期", "date", {"required": False, "date_format": "%Y-%m-%d"}, ["企业"]),
            ("legal_representative", "法定代表人", "string", {"required": False, "max_length": 50}, ["企业"]),
            ("credit_code", "统一社会信用代码", "string", {"required": False, "pattern": "^[A-Z0-9]{18}$"}, ["企业"]),
            ("industry", "所属行业", "enum", {"enum": ["银行", "证券", "保险", "信托", "基金", "房地产", "制造业", "科技", "能源", "商贸"]}, ["企业"]),
            ("risk_level", "风险等级", "enum", {"enum": ["低风险", "中低风险", "中风险", "高风险", "极高风险"]}, ["企业"]),
            ("annual_revenue", "年营业收入", "number", {"min": 0, "unit": "万元"}, ["企业"]),
            ("employee_count", "员工人数", "integer", {"min": 0}, ["企业"]),
            ("listing_status", "上市状态", "enum", {"enum": ["未上市", "主板", "创业板", "科创板", "港股", "美股"]}, ["企业"]),
            ("id_number", "身份证号", "string", {"pattern": "^\\d{17}[\\dX]$"}, ["个人"]),
            ("phone", "联系电话", "string", {"pattern": "^1\\d{10}$"}, ["企业", "个人"]),
            ("email", "邮箱", "email", {}, ["企业", "个人"]),
            ("address", "注册地址", "string", {"max_length": 500}, ["企业"]),
            ("shareholding_ratio", "持股比例", "number", {"min": 0, "max": 100, "unit": "%"}, ["个人"]),
            ("position", "职务", "string", {"max_length": 100}, ["个人"]),
        ]
        schema_map = {}
        for name, display_name, data_type, constraints, applies_to in schemas:
            sid = str(uuid.uuid4())
            s = AttributeSchema(
                id=sid, ontology_id=onto_id, name=name, display_name=display_name,
                data_type=data_type, constraints=constraints, applies_to_types=applies_to,
            )
            db.add(s)
            schema_map[name] = sid

        # ── 企业实体 ──
        companies = [
            ("华融控股集团有限公司", "Huarong Holdings", "企业", "bank", "国有大型金融控股集团", 1000000, "1999-01-01", "王建国", "91110000123456789A"),
            ("华融证券股份有限公司", "Huarong Securities", "企业", "证券", "综合性证券公司", 500000, "2007-09-07", "李明", "91110000710934567B"),
            ("华融信托有限责任公司", "Huarong Trust", "企业", "信托", "专业信托机构", 300000, "1987-06-05", "张伟", "91110000198765432C"),
            ("华融基金管理有限公司", "Huarong Fund", "企业", "基金", "公募基金管理公司", 100000, "2014-08-15", "刘洋", "91110000123456789D"),
            ("中信建设投资有限公司", "CITIC Construction", "企业", "房地产", "大型地产投资企业", 800000, "2005-03-18", "陈刚", "91110000567890123E"),
            ("鼎盛实业发展有限公司", "Dingsheng Industrial", "企业", "制造业", "制造业龙头企业", 200000, "2010-11-22", "赵强", "91110000345678901F"),
            ("新华科技集团有限公司", "Xinhua Tech", "企业", "科技", "科技创新企业", 150000, "2015-06-30", "孙磊", "91110000234567890G"),
            ("中海能源股份有限公司", "Zhonghai Energy", "企业", "能源", "新能源企业", 600000, "2008-04-12", "周涛", "91110000456789012H"),
            ("东方商贸集团有限公司", "Oriental Trading", "企业", "商贸", "大型商贸集团", 250000, "2000-09-20", "吴敏", "91110000543210987I"),
            ("太平洋保险集团有限公司", "Pacific Insurance", "企业", "保险", "综合性保险集团", 2000000, "2001-04-25", "郑华", "91110000678901234J"),
            ("华融资产投资有限公司", "Huarong Asset", "企业", "投资", "专业投资机构", 1800000, "1997-03-15", "黄志强", "91110000789012345K"),
            ("国盛担保有限公司", "Guosheng Guarantee", "企业", "担保", "融资担保机构", 100000, "2012-07-08", "林峰", "91110000890123456L"),
            ("长江建设集团有限公司", "Changjiang Construction", "企业", "房地产", "建筑与地产开发", 500000, "2003-05-14", "马超", "91110000901234567M"),
            ("鑫源控股股份有限公司", "Xinyuan Holdings", "企业", "投资", "民营控股公司", 300000, "2011-02-28", "徐静", "91110000111222333N"),
            ("东海银行股份有限公司", "Donghai Bank", "企业", "银行", "城市商业银行", 1500000, "1996-08-01", "朱伟", "91110000222333444O"),
        ]

        company_entities = {}
        for name, name_en, etype, industry, desc, capital, est_date, legal, credit in companies:
            eid = str(uuid.uuid4())
            e = Entity(
                id=eid, ontology_id=onto_id, name_cn=name, name_en=name_en,
                type=etype, description=desc,
                properties={
                    "industry": industry, "registered_capital": capital,
                    "establishment_date": est_date, "legal_representative": legal,
                    "credit_code": credit,
                    "risk_level": "中风险" if "华融" in name else "低风险",
                },
                confidence=1.0,
            )
            db.add(e)
            company_entities[name] = eid

        # ── 个人实体 ──
        persons = [
            ("王建国", "个人", "华融控股董事长兼党委书记", {"shareholding_ratio": 0.01, "position": "董事长"}),
            ("李明", "个人", "华融证券总经理", {"shareholding_ratio": 0.005, "position": "总经理"}),
            ("张伟", "个人", "华融信托董事长", {"shareholding_ratio": 0.008, "position": "董事长"}),
            ("刘洋", "个人", "华融基金总经理", {"shareholding_ratio": 0.003, "position": "总经理"}),
            ("陈刚", "个人", "中信建投董事长", {"shareholding_ratio": 0.015, "position": "董事长"}),
            ("赵强", "个人", "鼎盛实业实控人", {"shareholding_ratio": 68.5, "position": "实控人/董事长"}),
            ("孙磊", "个人", "新华科技CEO", {"shareholding_ratio": 45.2, "position": "CEO/创始人"}),
            ("周涛", "个人", "中海能源董事长", {"shareholding_ratio": 0.02, "position": "董事长"}),
            ("吴敏", "个人", "东方商贸总裁", {"shareholding_ratio": 35.0, "position": "总裁"}),
            ("郑华", "个人", "太平洋保险董事长", {"shareholding_ratio": 0.01, "position": "董事长"}),
            ("黄志强", "个人", "华融资产总裁", {"shareholding_ratio": 0.02, "position": "总裁"}),
            ("林峰", "个人", "国盛担保总经理", {"shareholding_ratio": 30.0, "position": "总经理"}),
        ]

        person_entities = {}
        for name, etype, desc, props in persons:
            eid = str(uuid.uuid4())
            e = Entity(
                id=eid, ontology_id=onto_id, name_cn=name, type=etype,
                description=desc, properties=props, confidence=1.0,
            )
            db.add(e)
            person_entities[name] = eid

        # ── 关系 ──
        relations_data = [
            # 股权关系
            ("华融控股集团有限公司", "华融证券股份有限公司", "控股", {"ratio": 52.3}),
            ("华融控股集团有限公司", "华融信托有限责任公司", "控股", {"ratio": 76.8}),
            ("华融控股集团有限公司", "华融基金管理有限公司", "控股", {"ratio": 100.0}),
            ("华融控股集团有限公司", "华融资产投资有限公司", "控股", {"ratio": 85.0}),
            ("华融资产投资有限公司", "中信建设投资有限公司", "参股", {"ratio": 23.5}),
            ("华融资产投资有限公司", "鼎盛实业发展有限公司", "参股", {"ratio": 15.0}),
            ("赵强", "鼎盛实业发展有限公司", "实控人", {"ratio": 68.5}),
            ("孙磊", "新华科技集团有限公司", "实控人", {"ratio": 45.2}),
            ("吴敏", "东方商贸集团有限公司", "实控人", {"ratio": 35.0}),
            ("林峰", "国盛担保有限公司", "实控人", {"ratio": 30.0}),
            # 担保关系
            ("国盛担保有限公司", "鼎盛实业发展有限公司", "担保", {"amount": 50000, "type": "融资担保"}),
            ("国盛担保有限公司", "新华科技集团有限公司", "担保", {"amount": 30000, "type": "融资担保"}),
            ("国盛担保有限公司", "东方商贸集团有限公司", "担保", {"amount": 80000, "type": "融资担保"}),
            ("华融证券股份有限公司", "鼎盛实业发展有限公司", "担保", {"amount": 200000, "type": "债券担保"}),
            # 关联交易
            ("华融证券股份有限公司", "华融信托有限责任公司", "关联交易", {"type": "资金往来"}),
            ("华融信托有限责任公司", "华融基金管理有限公司", "关联交易", {"type": "产品代销"}),
            ("中信建设投资有限公司", "长江建设集团有限公司", "合作", {"type": "项目合作"}),
            ("鑫源控股股份有限公司", "东海银行股份有限公司", "借贷", {"amount": 150000, "type": "贷款"}),
            ("新华科技集团有限公司", "中海能源股份有限公司", "供应", {"type": "技术服务"}),
            # 任职关系
            ("王建国", "华融控股集团有限公司", "任职", {"position": "董事长"}),
            ("李明", "华融证券股份有限公司", "任职", {"position": "总经理"}),
            ("张伟", "华融信托有限责任公司", "任职", {"position": "董事长"}),
            ("刘洋", "华融基金管理有限公司", "任职", {"position": "总经理"}),
            ("陈刚", "中信建设投资有限公司", "任职", {"position": "董事长"}),
            ("周涛", "中海能源股份有限公司", "任职", {"position": "董事长"}),
            ("郑华", "太平洋保险集团有限公司", "任职", {"position": "董事长"}),
            ("黄志强", "华融资产投资有限公司", "任职", {"position": "总裁"}),
            # 交叉担保链
            ("鼎盛实业发展有限公司", "新华科技集团有限公司", "互保", {"amount": 25000, "type": "互保协议"}),
            ("东方商贸集团有限公司", "中海能源股份有限公司", "互保", {"amount": 40000, "type": "互保协议"}),
            ("华融控股集团有限公司", "太平洋保险集团有限公司", "战略合作", {"type": "全面合作"}),
            ("东海银行股份有限公司", "国盛担保有限公司", "合作", {"type": "担保授信"}),
            ("鑫源控股股份有限公司", "中信建设投资有限公司", "投资", {"amount": 50000, "type": "股权投资"}),
        ]

        for src_name, tgt_name, rel_type, props in relations_data:
            src_id = company_entities.get(src_name) or person_entities.get(src_name)
            tgt_id = company_entities.get(tgt_name) or person_entities.get(tgt_name)
            if src_id and tgt_id:
                r = Relation(
                    id=str(uuid.uuid4()),
                    ontology_id=onto_id,
                    source_entity=src_id,
                    target_entity=tgt_id,
                    type=rel_type,
                    properties=props,
                    confidence=1.0,
                )
                db.add(r)

        # ── 逻辑规则 ──
        rules = [
            ("循环担保检测", "detect_cycle_guarantee", "检测A担保B、B担保C、C担保A的循环担保链", "MATCH (a)-[:担保]->(b)-[:担保]->(c)-[:担保]->(a) RETURN a,b,c"),
            ("超额担保预警", "excessive_guarantee", "检测单一企业对外担保金额超过注册资本200%的情况", "SUM(担保金额) > 注册资本 * 2"),
            ("实控人关联交易", "related_party_transaction", "检测实控人旗下企业间的关联交易", "实控人相同 AND 交易类型 IN [关联交易, 资金往来]"),
            ("高风险企业识别", "high_risk_enterprise", "识别同时满足:担保金额>1亿、互保关系>3家、风险等级=高风险 的企业", "担保金额 > 10000 AND 互保关系 > 3 AND 风险等级 = 高风险"),
            ("跨行业投资风险", "cross_industry_investment", "检测金融企业向非金融行业投资超过净资产30%的情况", "行业 = 金融 AND 目标行业 != 金融 AND 投资比例 > 0.3"),
        ]

        for name_cn, name_en, desc, formula in rules:
            lr = LogicRule(
                id=str(uuid.uuid4()),
                ontology_id=onto_id,
                name_cn=name_cn,
                name_en=name_en,
                description=desc,
                formula=formula,
                confidence=0.85,
                enabled=True,
                status="published",
            )
            db.add(lr)

        # ── 动作 ──
        actions_data = [
            ("生成风险事件", "create_risk_event", "规则命中时自动生成风险事件并通知风控团队", "CREATE_EVENT(type=risk, level=high, notify=风控团队)"),
            ("发送预警通知", "send_alert", "向相关人员发送预警通知", "SEND_NOTIFICATION(channel=email, template=risk_alert)"),
            ("调用外部Webhook", "call_webhook", "将检测结果推送到外部风控系统", "WEBHOOK(url=https://risk-api.example.com/alerts, method=POST)"),
        ]

        for name_cn, name_en, desc, rule in actions_data:
            a = Action(
                id=str(uuid.uuid4()),
                ontology_id=onto_id,
                name_cn=name_cn,
                name_en=name_en,
                description=desc,
                execution_rule=rule,
                confidence=0.9,
                enabled=True,
                status="published",
            )
            db.add(a)

        # ── 词表 ──
        vocab_data = [
            ("华融", ["华融集团", "华融系"], ["HR"], "企业"),
            ("控股", ["控制", "控股公司", "控股股东"], ["KG"], None),
            ("实控人", ["实际控制人", "最终控制人", "实际控制人"], ["SKR"], None),
            ("担保", ["保证", "担保人", "被担保"], ["DB"], None),
            ("关联交易", ["关联方交易", "内部交易"], ["GLJY"], None),
            ("融资担保", ["借款担保", "贷款担保"], ["RZDB"], None),
            ("注册资本", ["注册资金", "资本金"], ["ZCZB"], None),
            ("法定代表人", ["法人", "法人代表", "企业负责人"], ["FDDBR"], None),
            ("风险等级", ["风险级别", "风险分类"], ["FXDJ"], None),
            ("证券", ["券商", "证券公司"], ["ZQ"], None),
            ("信托", ["信托公司", "信托计划"], ["XT"], None),
            ("基金", ["基金公司", "基金管理"], ["JJ"], None),
            ("保险", ["保险公司", "保险集团"], ["BX"], None),
            ("银行", ["商业银行", "城商行"], ["YH"], None),
            ("互保", ["相互担保", "交叉担保", "连环担保"], ["HB"], None),
        ]

        for canonical, synonyms, abbrs, etype in vocab_data:
            v = VocabularyEntry(
                id=str(uuid.uuid4()),
                ontology_id=onto_id,
                canonical=canonical,
                synonyms=synonyms,
                abbreviations=abbrs,
                entity_type=etype,
                source="seed",
                confidence=1.0,
            )
            db.add(v)

        db.commit()
        print(f"Seeded ontology: {onto_id}")
        print(f"  Companies: {len(companies)}")
        print(f"  Persons: {len(persons)}")
        print(f"  Relations: {len(relations_data)}")
        print(f"  Rules: {len(rules)}")
        print(f"  Actions: {len(actions_data)}")
        print(f"  Schemas: {len(schemas)}")
        print(f"  Vocab: {len(vocab_data)}")

    finally:
        db.close()


if __name__ == "__main__":
    seed()
