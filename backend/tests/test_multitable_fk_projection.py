"""真实场景：AI HOT items 拆成两张表（Source / NewsItem），
验证多表 FK 链接自动推断 + 投影出 LinkType / LinkInstance。

NewsItem.source 列引用 Source.source_name（同名/引用列 FK 场景）。
"""
import json
import sys
import uuid

import app.main  # noqa: F401  确保全部模型注册，FK 可解析
from app.database import SessionLocal
from app.services.v2.dataset_service import DatasetService
from app.services.v2.mapping.mapping_service import MappingService
from app.services.collectors import aihot
from app.models.ontology import OntologyProject
from app.models.user import User
from app.models.ontology_formal import ObjectType, LinkType, LinkInstance


def main():
    db = SessionLocal()
    svc = MappingService(db)
    ds_svc = DatasetService(db)

    # 1) 拉真实 AI HOT 数据
    payload = aihot.fetch_items(take=40)
    items = payload.get("items", []) if isinstance(payload, dict) else payload
    assert items, "AI HOT 拉取为空"
    print(f"[1] 拉取 AI HOT items: {len(items)} 条")

    # 2) 派生两张表
    sources = {}
    news_rows = []
    for it in items:
        props = aihot.item_to_properties(it) if hasattr(aihot, "item_to_properties") else it
        src_name = str(props.get("source") or "Unknown").strip()
        sources.setdefault(src_name, {"source_name": src_name, "kind": "RSS"})
        news_rows.append({
            "id": str(props.get("id") or uuid.uuid4()),
            "title": props.get("title"),
            "source": src_name,          # FK -> Source.source_name
            "category": props.get("category"),
            "score": props.get("score"),
        })
    source_rows = list(sources.values())
    print(f"[2] 派生 Source={len(source_rows)} 张, NewsItem={len(news_rows)} 行")

    # 3) 落地两张 curated dataset
    ds_src = ds_svc.create_dataset(name=f"FKTest_Source_{uuid.uuid4().hex[:6]}", kind="curated")
    ds_svc.create_version(ds_src.id, json.dumps(source_rows).encode(), rowcount=len(source_rows))
    ds_news = ds_svc.create_dataset(name=f"FKTest_News_{uuid.uuid4().hex[:6]}", kind="curated")
    ds_svc.create_version(ds_news.id, json.dumps(news_rows).encode(), rowcount=len(news_rows))
    print(f"[3] 落地 curated: source={ds_src.id} news={ds_news.id}")

    # 4) 新建本体 + 两个 mapping
    user = db.query(User).first()
    onto = OntologyProject(
        name=f"FK多表验证_{uuid.uuid4().hex[:6]}",
        domain="资讯", description="multi-table fk test",
        created_by=user.id,
    )
    db.add(onto); db.commit(); db.refresh(onto)
    oid = onto.id

    svc.create_mapping(oid, ds_src.id, "Source",
                       field_mapping={"__primary_key__": "source_name"},
                       primary_key_column="source_name")
    svc.create_mapping(oid, ds_news.id, "NewsItem",
                       field_mapping={"__primary_key__": "id"},
                       primary_key_column="id")
    print(f"[4] 本体 {oid} + 2 mapping")

    # 5) build_all
    result = svc.build_all(oid)
    rels = result.get("relations_written", [])
    print(f"[5] build_all: relations_written={rels}")
    print(f"    formal_projection={result.get('formal_projection')}")

    # 6) 断言：投影出 2 个 ObjectType + 至少 1 个 LinkType + LinkInstance
    ots = db.query(ObjectType).filter(ObjectType.ontology_id == oid).all()
    lts = db.query(LinkType).filter(LinkType.ontology_id == oid).all()
    lis = db.query(LinkInstance).filter(LinkInstance.ontology_id == oid).all()
    print(f"[6] ObjectType={len(ots)} LinkType={len(lts)} LinkInstance={len(lis)}")
    for lt in lts:
        print(f"    LinkType: {lt.name} card={lt.cardinality} {lt.source_role}->{lt.target_role}")

    ok = len(ots) >= 2 and len(lts) >= 1 and len(lis) >= 1
    print("RESULT:", "PASS" if ok else "FAIL")
    db.close()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
