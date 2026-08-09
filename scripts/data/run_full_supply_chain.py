#!/usr/bin/env python3
"""
供应链数据接入与映射全流程脚本 v2
- Pipeline: python 引擎（脚本内嵌演示数据；canvas DAG 与 LLM/VLM 转换步骤已下线）
- Ontology: 创建手工建模项目，再由 pipeline mapping 写入实例数据
- Neo4j: mapping apply 时自动写入图数据库

本脚本不会调用已退役的 Prompt、旧本体文件上传或文档→本体抽取接口。
"""
import os
import httpx

BASE_URL = "http://localhost:8000"
DATA_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "test_data", "供应链")
)

# 文件列表（原始数据集上传，供页面查看；route A/B/C 引擎已下线，
# Pipeline 不再按文件类型路由转换）
FILES = [
    "inventory_transactions.csv",
    "logistics_performance.csv",
    "supplier_database.xlsx",
    "supplier_orders.json",
    "procurement_policy.docx",
    "supply_chain_review.pptx",
    "supply_chain_strategy.md",
    "warehouse_management.pdf",
]

MIME = {
    "csv":  "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "json": "application/json",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "md":   "text/markdown",
    "pdf":  "application/pdf",
}


def login(c):
    r = c.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    r.raise_for_status()
    return r.json()["data"]["access_token"]


def h(token):
    return {"Authorization": f"Bearer {token}"}


# ─── 清理旧数据 ───────────────────────────────────────────────
def cleanup_old_data(c, token):
    """删除旧的供应链 pipeline 和 ontology，保留模型配置"""
    # 删除旧 pipelines
    pls = c.get("/api/v2/pipelines", headers=h(token)).json()
    for pl in pls:
        if "供应链" in pl.get("name", ""):
            c.delete(f"/api/v2/pipelines/{pl['id']}", headers=h(token))
            print(f"  [DEL] Pipeline: {pl['name']}")
    # 删除旧 ontologies
    onts = c.get("/api/v1/ontologies", headers=h(token)).json()
    for o in onts.get("data", {}).get("items", []):
        if "供应链" in o.get("name", ""):
            c.delete(f"/api/v1/ontologies/{o['id']}", headers=h(token))
            print(f"  [DEL] Ontology: {o['name']}")


# ─── Step 1: 上传文件 ─────────────────────────────────────────
def upload_files(c, token):
    datasets = {}
    for fname in FILES:
        fpath = os.path.join(DATA_DIR, fname)
        if not os.path.exists(fpath):
            print(f"  [SKIP] {fname}")
            continue
        ext = fname.rsplit(".", 1)[-1].lower()
        with open(fpath, "rb") as f:
            content = f.read()
        r = c.post("/api/v2/datasets/upload", headers=h(token),
                   files={"file": (fname, content, MIME.get(ext, "application/octet-stream"))})
        r.raise_for_status()
        ds = r.json()["data"]
        datasets[fname] = {"id": ds["id"], "kind": ds["kind"]}
        print(f"  [OK] {fname} → {ds['id'][:8]}... kind={ds['kind']}")
    return datasets


# ─── Step 2: 创建 Pipeline ────────────────────────────────────
# python 引擎契约：脚本在内核内自洽取数，最终结果赋值给 result（list[dict]）。
# 原 canvas DAG 的 LLM/VLM 文档提取与 JSON 展开步骤已随引擎下线，此处用
# 内嵌演示数据直通（含 supplier.* 点分列，演示嵌套展开的等价结果形态）。
PIPELINE_SCRIPT = """\
# 供应链演示数据（直通；canvas 引擎与 LLM/VLM 转换步骤已下线）
result = [
    {"日期": "2026-03-08", "物料编码": "MAT001", "操作类型": "出库",
     "数量": 62, "库存状态": "正常", "所在仓库": "WH-A"},
    {"日期": "2026-03-09", "物料编码": "MAT002", "操作类型": "入库",
     "数量": 120, "库存状态": "正常", "所在仓库": "WH-B"},
    {"日期": "2026-03-10", "物料编码": "MAT001", "操作类型": "盘点",
     "数量": 185, "库存状态": "短缺", "所在仓库": "WH-A"},
]
"""


def create_pipeline(c, token):
    r = c.post("/api/v2/pipelines", headers=h(token), json={
        "name": "供应链全链路Pipeline",
        "domain": "供应链",
        "description": "供应链数据接入（python 引擎演示；canvas 已下线）",
        "definition": {
            "engine": "python",
            "python": {"script": PIPELINE_SCRIPT},
        },
    })
    if r.status_code == 400 and "已存在" in r.text:
        pls = c.get("/api/v2/pipelines", headers=h(token)).json()
        for pl in pls:
            if pl["name"] == "供应链全链路Pipeline":
                return pl["id"]
    r.raise_for_status()
    pid = r.json()["id"]
    print(f"  [OK] Pipeline id={pid}")
    return pid


# ─── Step 3: 运行 Pipeline ────────────────────────────────────
def run_pipeline(c, token, pipeline_id):
    r = c.post(f"/api/v2/pipelines/{pipeline_id}/run-sync", headers=h(token), timeout=300)
    r.raise_for_status()
    result = r.json()
    stats = result.get("stats") or {}
    curated_ids = stats.get("curated_dataset_ids") or []
    if not curated_ids and stats.get("curated_dataset_id"):
        curated_ids = [stats["curated_dataset_id"]]
    print(f"  [OK] status={result.get('status')} rows_in={stats.get('rows_in',0)} rows_out={stats.get('rows_out',0)}")
    print(f"  [OK] 生成 {len(curated_ids)} 个 Curated Dataset")

    # 打印每个 curated dataset 预览
    for cid in curated_ids[:4]:
        rows = c.get(f"/api/v2/datasets/{cid}/versions/1/preview?limit=2", headers=h(token))
        if rows.status_code == 200:
            data = rows.json()
            if data:
                cols = list(data[0].keys())[:5]
                print(f"    - {cid[:8]}... cols={cols}")
    return curated_ids


# ─── Step 4: 创建本体项目 ────────────────────────────────────
def create_ontology(c, token):
    r = c.post("/api/v1/ontologies", headers=h(token), json={
        "name": "供应链知识本体 v2",
        "domain": "供应链",
        "description": "手工建模后由 Pipeline Mapping 写入实例数据的供应链知识本体",
        "build_mode": "manual",
    })
    r.raise_for_status()
    oid = r.json()["data"]["id"]
    print(f"  [OK] Ontology id={oid}")
    return oid


# ─── Step 5: 自动映射 ────────────────────────────────────────
def auto_map_and_create(c, token, ontology_id):
    """对每个 curated dataset 调用 suggest + create mapping"""
    curated_list = c.get("/api/v2/curated", headers=h(token)).json()
    mappings_created = []

    for ds in curated_list:
        cid = ds["id"]
        name = ds["name"]

        # 获取 schema 信息
        schema_r = c.get(f"/api/v2/datasets/{cid}/schema", headers=h(token))
        if schema_r.status_code != 200:
            continue
        schema = schema_r.json()
        columns = [col["name"] for col in schema.get("columns", [])]
        if not columns:
            continue

        # 获取样本数据
        preview_r = c.get(f"/api/v2/datasets/{cid}/versions/1/preview?limit=3", headers=h(token))
        sample_rows = preview_r.json() if preview_r.status_code == 200 else []

        # LLM suggest mapping
        suggest_r = c.post(
            f"/api/v2/ontologies/{ontology_id}/mappings/suggest",
            headers=h(token),
            json={
                "dataset_name": name,
                "columns": columns,
                "sample_rows": sample_rows,
                "ontology_domain": "供应链",
            },
            timeout=60,
        )
        if suggest_r.status_code != 200:
            print(f"  [WARN] suggest 失败 {name}: {suggest_r.status_code}")
            continue
        suggestion = suggest_r.json()
        entity_class = suggestion.get("entity_class", "UnknownEntity")
        pk_col = suggestion.get("primary_key_column")
        field_mapping = {
            fm["column_name"]: fm["property_name"]
            for fm in suggestion.get("field_mappings", [])
        }
        print(f"  [SUGGEST] {name[:40]} → {entity_class} pk={pk_col}")

        # 创建 mapping
        create_r = c.post(
            f"/api/v2/ontologies/{ontology_id}/mappings",
            headers=h(token),
            json={
                "curated_dataset_id": cid,
                "entity_class": entity_class,
                "field_mapping": field_mapping,
                "primary_key_column": pk_col,
                "confidence": 0.92,
            },
        )
        if create_r.status_code in (200, 201):
            mid = create_r.json().get("mapping_id")
            mappings_created.append({"id": mid, "entity_class": entity_class, "curated_id": cid})
            print(f"  [OK] Mapping created: {entity_class} mapping_id={mid}")
        else:
            print(f"  [WARN] create mapping 失败: {create_r.status_code} {create_r.text[:80]}")

    return mappings_created


# ─── Step 6: Apply Mappings → Neo4j ──────────────────────────
def apply_mappings_to_neo4j(c, token, ontology_id, mappings):
    """触发 apply-from-dataset，直接从 curated dataset 读取并写入 Neo4j"""
    all_mappings = c.get(f"/api/v2/ontologies/{ontology_id}/mappings", headers=h(token)).json()
    applied = 0
    seen_curated = set()
    for mapping in all_mappings:
        mid = mapping.get("id") or mapping.get("mapping_id")
        cid = mapping.get("curated_dataset_id")
        if not mid or not cid:
            continue
        # 同一 curated dataset 只 apply 一次
        if cid in seen_curated:
            continue
        seen_curated.add(cid)
        apply_r = c.post(
            f"/api/v2/ontologies/{ontology_id}/mappings/{mid}/apply-from-dataset",
            headers=h(token),
            timeout=120,
        )
        if apply_r.status_code in (200, 201):
            res = apply_r.json()
            neo4j = res.get("neo4j_count", 0)
            v1 = res.get("v1_count", 0)
            print(f"  [OK] {mapping.get('entity_class'):30s} neo4j={neo4j} v1={v1}")
            applied += 1
        else:
            print(f"  [WARN] {mid[:8]}...: {apply_r.status_code} {apply_r.text[:80]}")
    return applied


# ─── Step 7: 验证 ────────────────────────────────────────────
def verify(c, token, ontology_id):
    entities = c.get(f"/api/v1/ontologies/{ontology_id}/entities", headers=h(token)).json()
    logic    = c.get(f"/api/v1/ontologies/{ontology_id}/logic",    headers=h(token)).json()
    actions  = c.get(f"/api/v1/ontologies/{ontology_id}/actions",  headers=h(token)).json()
    mappings = c.get(f"/api/v2/ontologies/{ontology_id}/mappings", headers=h(token)).json()

    n_e = len(entities.get("data", []))
    n_l = len(logic.get("data", []))
    n_a = len(actions.get("data", []))
    n_m = len(mappings) if isinstance(mappings, list) else 0

    # Neo4j 图验证
    neo4j_ok = False
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "ontoprompt123"))
        with driver.session() as s:
            node_count = s.run("MATCH (n) RETURN count(n) as c").single()["c"]
            rel_count  = s.run("MATCH ()-[r]->() RETURN count(r) as c").single()["c"]
        driver.close()
        neo4j_ok = node_count > 0
        neo4j_info = f"nodes={node_count} relations={rel_count}"
    except Exception as e:
        neo4j_info = f"error: {e}"

    print("\n" + "=" * 60)
    print("验收结果")
    print("=" * 60)
    print(f"  实体:     {n_e} 个")
    print(f"  逻辑规则: {n_l} 条")
    print(f"  Action:   {n_a} 个")
    print(f"  映射:     {n_m} 条")
    print(f"  Neo4j:    {neo4j_info}")
    print()
    checks = [
        ("知识图谱网络状结构(≥5实体)", n_e >= 5),
        ("映射关系建立(≥1条)", n_m >= 1),
        ("Neo4j 图数据写入", neo4j_ok),
    ]
    all_pass = True
    for label, passed in checks:
        icon = "✓" if passed else "✗"
        print(f"  {icon} {label}")
        if not passed:
            all_pass = False
    print()
    print("  [SUCCESS] 全部通过！" if all_pass else "  [PARTIAL] 部分未通过")
    return {"entities": n_e, "logic": n_l, "actions": n_a, "mappings": n_m, "neo4j": neo4j_info}


# ─── Main ─────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("供应链 全流程 Pipeline + Ontology (v2)")
    print("Pipeline: python 引擎  |  Mapping: LLM 建议  |  Graph: Neo4j")
    print("=" * 60)

    with httpx.Client(base_url=BASE_URL, timeout=300) as c:
        print("\n[Auth] 登录...")
        token = login(c)
        print("  [OK]")

        # 模型配置仅影响 Step 5 的 LLM 映射建议（服务端取用）；Pipeline 本身
        # 已是 python 引擎直通脚本，不再消费 LLM/VLM 模型配置。
        models = c.get("/api/v1/models", headers=h(token)).json()
        models = models.get("data", []) if isinstance(models, dict) else models
        llm_cfg = next((m for m in models if "结构化提取" in (m.get("options") or {}).get("usage_tags", [])), None)
        if llm_cfg:
            print(f"  LLM: {llm_cfg['name']} / {(llm_cfg.get('models') or ['?'])[0]}")
        else:
            print("  [WARN] 未找到带「结构化提取」标签的模型配置，Step 5 映射建议可能失败")

        print("\n[Step 0] 清理旧供应链数据...")
        cleanup_old_data(c, token)

        print("\n[Step 1] 上传文件 → Datasets...")
        datasets = upload_files(c, token)
        print(f"  [OK] 共 {len(datasets)} 个文件")

        print("\n[Step 2] 创建 Pipeline (python 引擎)...")
        pipeline_id = create_pipeline(c, token)

        print("\n[Step 3] 运行 Pipeline...")
        curated_ids = run_pipeline(c, token, pipeline_id)

        print("\n[Step 4] 创建本体项目...")
        ontology_id = create_ontology(c, token)

        print("\n[Step 5] LLM 自动映射...")
        mappings = auto_map_and_create(c, token, ontology_id)
        print(f"  [OK] 创建 {len(mappings)} 条映射")

        print("\n[Step 6] Apply Mappings → Neo4j...")
        applied = apply_mappings_to_neo4j(c, token, ontology_id, mappings)
        print(f"  [OK] {applied} 条映射已写入 Neo4j")

        print("\n[Step 7] 验证...")
        result = verify(c, token, ontology_id)

        print("\n" + "=" * 60)
        print(f"Pipeline ID:  {pipeline_id}")
        print(f"Ontology ID:  {ontology_id}")
        print(f"Curated 数量: {len(curated_ids)}")
        print(f"Mappings:     {result['mappings']}")
        print(f"Neo4j:        {result['neo4j']}")
        print("=" * 60)


if __name__ == "__main__":
    main()
