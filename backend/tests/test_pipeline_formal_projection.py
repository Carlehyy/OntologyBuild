"""
端到端：AI HOT → 数据流水线落地 → 正规本体投影

验证 Direction 1 的核心 gap 修复：
  1. 用 AI HOT connector 拉真实资讯数据
  2. 落成 Dataset version (流水线 curated 数据)
  3. 建 OntologyMapping，build_all() 写 Entity/Relation
  4. Phase 5 投影出 ObjectType/ObjectInstance/LinkType/LinkInstance
  5. 校验流水线数据已可在正规本体 (图谱编辑器数据源) 中看到

直接走数据库 + service 层，不依赖外部 broker。
"""
import json
import sys
import time

# 让 backend 包可导入
sys.path.insert(0, ".")

from app.database import SessionLocal, engine, Base
import app.models  # noqa: F401  确保所有表注册
import app.models.v2.connection  # noqa: F401
import app.models.v2.curated  # noqa: F401
import app.models.v2.pipeline  # noqa: F401
from app.models.ontology import OntologyProject
from app.models.entity import Entity
from app.models.relation import Relation
from app.models.ontology_formal import (
    ObjectType, ObjectInstance, LinkType, LinkInstance,
)
from app.models.v2.dataset import Dataset, DatasetVersion
from app.models.v2.mapping import OntologyMapping
from app.services.connection.registry import get_connector
from app.services.v2.dataset_service import DatasetService
from app.services.v2.mapping.mapping_service import MappingService


def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        # 0. 本体
        from app.models.user import User
        admin = db.query(User).first()
        assert admin, "无可用用户"
        oid = f"pl-formal-{int(time.time())}"
        db.add(OntologyProject(id=oid, name="AIHOT流水线投影E2E", domain="科技",
                               description="pipeline→formal projection",
                               created_by=admin.id))
        db.commit()
        print(f"✓ ontology {oid}")

        # 1. AI HOT 拉真实数据
        conn = get_connector("aihot", {"mode": "selected", "take": 30})
        assert conn.test_connection(), "AI HOT 连接失败"
        rows = conn.pull_full("items")
        assert rows, "AI HOT 未返回数据"
        print(f"✓ AI HOT 拉取 {len(rows)} 条真实资讯")

        # 2. 落成 Dataset version（模拟 curated 数据）
        ds_svc = DatasetService(db)
        ds = ds_svc.create_dataset(name="aihot_items", kind="structured")
        payload = json.dumps(rows, ensure_ascii=False).encode("utf-8")
        ds_svc.create_version(ds.id, payload, rowcount=len(rows))
        preview = ds_svc.preview(ds.id, 1, limit=5)
        assert preview, "数据集预览为空"
        print(f"✓ dataset {ds.id} v1 ({len(rows)} rows)")

        # 3. 建 mapping：NewsItem 对象，主键 id
        # field_mapping: 源列 -> 属性名（同名直映 + 几个关键字段）
        sample_cols = list(rows[0].keys())
        field_mapping = {col: col for col in sample_cols}
        field_mapping["__primary_key__"] = "id"
        field_mapping["__properties__"] = [
            {"column": "title", "property": "title", "type": "string", "display_name": "标题"},
            {"column": "source", "property": "source", "type": "string", "display_name": "信源"},
            {"column": "category", "property": "category", "type": "string", "display_name": "分类"},
            {"column": "score", "property": "score", "type": "number", "display_name": "评分"},
        ]
        mp_svc = MappingService(db)
        mp = mp_svc.create_mapping(
            ontology_id=oid,
            curated_dataset_id=ds.id,
            entity_class="NewsItem",
            field_mapping=field_mapping,
            primary_key_column="id",
        )
        print(f"✓ mapping {mp.id} (NewsItem)")

        # 第二张表：信源 Source（从同批数据按 source 去重）
        sources = {}
        for r in rows:
            s = r.get("source")
            if s and s not in sources:
                sources[s] = {"id": s, "name": s, "source": s}
        src_rows = list(sources.values())
        ds2 = ds_svc.create_dataset(name="aihot_sources", kind="structured")
        ds_svc.create_version(ds2.id, json.dumps(src_rows, ensure_ascii=False).encode("utf-8"),
                              rowcount=len(src_rows))
        mp2 = mp_svc.create_mapping(
            ontology_id=oid,
            curated_dataset_id=ds2.id,
            entity_class="Source",
            field_mapping={"id": "id", "name": "name", "source": "source",
                           "__primary_key__": "id"},
            primary_key_column="id",
        )
        print(f"✓ mapping {mp2.id} (Source, {len(src_rows)} sources)")

        # 4. build_all → 应触发 Phase 5 投影
        result = mp_svc.build_all(oid)
        print(f"✓ build_all 完成: entities={result.get('total_entities')} "
              f"relations={result.get('total_relations')}")
        proj = result.get("formal_projection") or {}
        print(f"  formal_projection: {proj}")

        # 5. 校验旧表
        n_entity = db.query(Entity).filter(Entity.ontology_id == oid).count()
        n_rel = db.query(Relation).filter(Relation.ontology_id == oid).count()
        print(f"  旧表: Entity={n_entity} Relation={n_rel}")
        assert n_entity > 0, "未写入 Entity"

        # 6. 校验正规本体投影
        n_ot = db.query(ObjectType).filter(ObjectType.ontology_id == oid).count()
        n_oi = db.query(ObjectInstance).filter(ObjectInstance.ontology_id == oid).count()
        n_lt = db.query(LinkType).filter(LinkType.ontology_id == oid).count()
        n_li = db.query(LinkInstance).filter(LinkInstance.ontology_id == oid).count()
        print(f"  正规本体: ObjectType={n_ot} ObjectInstance={n_oi} "
              f"LinkType={n_lt} LinkInstance={n_li}")
        assert n_ot >= 2, f"ObjectType 投影不足: {n_ot}"
        assert n_oi == n_entity, f"ObjectInstance({n_oi}) != Entity({n_entity})"

        # 7. 校验 ObjectInstance 属性可用 + 溯源
        inst = db.query(ObjectInstance).filter(
            ObjectInstance.ontology_id == oid,
        ).first()
        assert inst.source == "pipeline", "实例溯源标记缺失"
        assert inst.external_id, "external_id 缺失（去重依赖）"
        assert isinstance(inst.properties, dict) and inst.properties, "实例属性为空"
        print(f"  样本实例属性键: {list(inst.properties.keys())[:6]}")

        # 8. 幂等性：再跑一次 build_all，投影计数应保持稳定（upsert 不翻倍）
        mp_svc2 = MappingService(SessionLocal())
        mp_svc2.build_all(oid)
        n_oi2 = db.query(ObjectInstance).filter(ObjectInstance.ontology_id == oid).count()
        # 重开 session 读最新
        db.expire_all()
        n_oi2 = db.query(ObjectInstance).filter(ObjectInstance.ontology_id == oid).count()
        assert n_oi2 == n_oi, f"幂等性失败: 重跑后 ObjectInstance {n_oi}->{n_oi2}"
        print(f"✓ 幂等性: 重跑后 ObjectInstance 仍为 {n_oi2}")

        print("\n✅ ALL CHECKS PASSED — AI HOT → 流水线 → 正规本体 全链路打通")
        print(f"   验证本体 id: {oid}")
        return oid
    finally:
        db.close()


if __name__ == "__main__":
    main()
