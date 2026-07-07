"""
直接调用提取逻辑（绕过Celery），对各领域本体进行提取，记录时长与质量指标。
用法：cd backend && python scripts/dev/run_extraction_direct.py
"""
import sys, os, time, uuid, json
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(_ROOT)
sys.path.insert(0, _ROOT)

from app.database import SessionLocal
from app.models.ontology import OntologyProject
from app.models.file import UploadedFile
from app.models.model_config import ModelConfig
from app.models.prompt import Prompt
from app.models.entity import Entity
from app.models.logic import LogicRule
from app.models.action import Action
from app.models.relation import Relation
from app.services.llm_service import extract_ontology, infer_relations
from app.services.encryption_service import decrypt
from app.tasks.extraction import _calibrate_confidence, _dedup_existing, _fuzzy_resolve_entity
import re as _re

# ── 配置：各领域测试用例 ───────────────────────────────────────────────────
# (ontology_id, prompt_id, model_id, model_name, label)
TEST_CASES = [
    ("ef1a1be8-d336-4c82-af43-eddd9fe75019", "bfe0bb61-0f26-46f3-93d9-0c515c0e813e", None, None, "供应链(5文件)"),
    ("75d5e5f7-c101-49a6-a2ed-91642d6a0dcc", "779bd973-30ee-4ffd-9bfc-27e94f180fef", None, None, "HR"),
    ("ee16adcf-ca0d-44b8-8698-67c7e8242dec", "a1114d03-a3d3-4a76-8602-6744b562ef52", None, None, "财务"),
    ("5234b7f3-2e3d-4536-b2ce-f48508b596a4", "db39ed80-3361-4c5e-9e1d-672810ab89fa", None, None, "营销"),
    ("f3fa7adb-9ad0-44a9-aa98-b6869f315242", "572a5295-e68e-4629-875a-9df2ceae6056", None, None, "医疗"),
]

def get_deepseek_model(db):
    """Find first DeepSeek model config."""
    models = db.query(ModelConfig).all()
    for m in models:
        if 'deepseek' in (m.provider or '').lower() or 'deepseek' in (m.name or '').lower():
            return m
    return models[0] if models else None

def run_one(db, oid, prompt_id, model_cfg, model_name_override, label):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")

    # Load files
    files = db.query(UploadedFile).filter(UploadedFile.ontology_id == oid).all()
    if not files:
        print(f"  ❌ 无文件，跳过")
        return None
    file_names = [f.filename for f in files]
    print(f"  文件: {', '.join(file_names)}")

    combined_text = "\n\n---\n\n".join(f.converted_md or "" for f in files if f.converted_md)
    if not combined_text.strip():
        print(f"  ❌ 文件无文本内容，跳过")
        return None
    combined_text = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', combined_text)
    print(f"  文本长度: {len(combined_text):,} chars")

    prompt = db.query(Prompt).filter(Prompt.id == prompt_id).first()
    if not prompt:
        print(f"  ❌ Prompt 未找到: {prompt_id}")
        return None
    print(f"  Prompt: {prompt.name} ({prompt.version})")

    # Model
    if not model_cfg:
        model_cfg = get_deepseek_model(db)
    if not model_cfg:
        print("  ❌ 无模型配置")
        return None
    model_name = model_name_override or (model_cfg.models[0] if model_cfg.models else "")
    print(f"  模型: {model_cfg.name} / {model_name}")

    config_dict = {
        "provider": model_cfg.provider,
        "api_key":  decrypt(model_cfg.api_key_encrypted or ""),
        "api_base": model_cfg.api_base,
    }

    # ── Pass 1: LLM extraction ──────────────────────────────────────────────
    print(f"  ▶ 开始提取...")
    t0 = time.time()
    try:
        result = extract_ontology(combined_text, prompt.content, config_dict, model_name)
    except Exception as e:
        print(f"  ❌ LLM 调用失败: {e}")
        return None
    t_llm = time.time() - t0
    print(f"  Pass 1 完成 ({t_llm:.1f}s): "
          f"{len(result.get('entities',[]))}实体 "
          f"{len(result.get('logic_rules',[]))}规则 "
          f"{len(result.get('actions',[]))}动作")

    result = _calibrate_confidence(result)

    # ── Pass 2: infer relations if sparse ──────────────────────────────────
    entities_raw  = result.get("entities", [])
    relations_raw = result.get("relations", [])
    entity_names_set = {e.get("name_cn") for e in entities_raw if e.get("name_cn")}
    in_relation: set = set()
    for r in relations_raw:
        in_relation.add(r.get("source", "")); in_relation.add(r.get("target", ""))
    isolated_count = sum(1 for n in entity_names_set if n and not any(
        n in rn or rn in n for rn in in_relation if rn))
    sparse = len(relations_raw) < max(5, len(entities_raw) * 0.4)
    many_isolated = isolated_count > max(2, len(entities_raw) * 0.3)

    t_infer = 0.0
    if len(entities_raw) >= 5 and (sparse or many_isolated):
        print(f"  ▶ 关系稀疏（{len(relations_raw)}条/{len(entities_raw)}实体），触发 Pass 2...")
        t1 = time.time()
        try:
            extra = infer_relations(entities_raw, relations_raw, combined_text, config_dict, model_name)
            for r in (extra or []):
                src, tgt = r.get("source",""), r.get("target","")
                src_ok = src in entity_names_set or any(src in n or n in src for n in entity_names_set if n)
                tgt_ok = tgt in entity_names_set or any(tgt in n or n in tgt for n in entity_names_set if n)
                if src_ok and tgt_ok:
                    result["relations"].append(r)
            result = _calibrate_confidence(result)
        except Exception as e:
            print(f"  ⚠ Pass 2 失败: {e}")
        t_infer = time.time() - t1
        print(f"  Pass 2 完成 ({t_infer:.1f}s): {len(result.get('relations',[]))}条关系")

    t_total = time.time() - t0

    # ── Save to DB ──────────────────────────────────────────────────────────
    print(f"  ▶ 写入数据库...")
    _dedup_existing(db, oid, Entity, "name_cn")
    _dedup_existing(db, oid, LogicRule, "name_cn")
    _dedup_existing(db, oid, Action, "name_cn")
    db.flush()

    existing_entities = db.query(Entity).filter(Entity.ontology_id == oid).all()
    existing_ent_map  = {e.name_cn: e for e in existing_entities}
    entity_name_to_id = {e.name_cn: e.id for e in existing_entities}
    for e in existing_entities:
        if e.name_en: entity_name_to_id[e.name_en] = e.id

    for e_data in result.get("entities", []):
        name_cn = e_data.get("name_cn") or ""
        if not name_cn: continue
        props = e_data.get("properties") or {}
        if not isinstance(props, dict): props = {}
        if name_cn in existing_ent_map:
            ent = existing_ent_map[name_cn]
            if e_data.get("type"):        ent.type        = e_data["type"]
            if e_data.get("description"): ent.description = e_data["description"]
            if props:                     ent.properties  = props
            if e_data.get("name_en"):     ent.name_en     = e_data["name_en"]
            ent.confidence = e_data.get("confidence", ent.confidence)
            eid = ent.id
        else:
            eid = str(uuid.uuid4())
            ent = Entity(id=eid, ontology_id=oid,
                         name_cn=name_cn, name_en=e_data.get("name_en"),
                         type=e_data.get("type"), description=e_data.get("description"),
                         properties=props, confidence=e_data.get("confidence", 0.85))
            db.add(ent)
            existing_ent_map[name_cn] = ent
        entity_name_to_id[name_cn] = eid
        if e_data.get("name_en"): entity_name_to_id[e_data["name_en"]] = eid

    # Relations
    existing_rels    = db.query(Relation).filter(Relation.ontology_id == oid).all()
    existing_rel_set = {(r.source_entity, r.target_entity, r.type) for r in existing_rels}
    for rel in result.get("relations", []):
        src_id  = _fuzzy_resolve_entity(rel.get("source",""), entity_name_to_id)
        tgt_id  = _fuzzy_resolve_entity(rel.get("target",""), entity_name_to_id)
        rtype   = rel.get("type","关联")
        if src_id and tgt_id and (src_id,tgt_id,rtype) not in existing_rel_set:
            db.add(Relation(id=str(uuid.uuid4()), ontology_id=oid,
                            source_entity=src_id, target_entity=tgt_id,
                            type=rtype, confidence=rel.get("confidence",0.85)))
            existing_rel_set.add((src_id,tgt_id,rtype))

    # Logic rules
    existing_rules   = db.query(LogicRule).filter(LogicRule.ontology_id == oid).all()
    existing_rule_map = {r.name_cn: r for r in existing_rules}
    for r_data in result.get("logic_rules", []):
        name_cn = r_data.get("name_cn","")
        if not name_cn: continue
        le = [entity_name_to_id.get(n) for n in (r_data.get("linked_entities") or []) if entity_name_to_id.get(n)]
        if name_cn in existing_rule_map:
            rule = existing_rule_map[name_cn]
            if r_data.get("formula"):     rule.formula     = r_data["formula"]
            if r_data.get("description"): rule.description = r_data["description"]
            if le:                        rule.linked_entities = le
            rule.confidence = r_data.get("confidence", rule.confidence)
        else:
            db.add(LogicRule(id=str(uuid.uuid4()), ontology_id=oid,
                             name_cn=name_cn, name_en=r_data.get("name_en"),
                             formula=r_data.get("formula",""), description=r_data.get("description"),
                             confidence=r_data.get("confidence",0.85), linked_entities=le))

    # Actions
    all_rules_after = db.query(LogicRule).filter(LogicRule.ontology_id == oid).all()
    rule_name_to_id = {r.name_cn: r.id for r in all_rules_after}
    existing_actions = db.query(Action).filter(Action.ontology_id == oid).all()
    existing_act_map = {a.name_cn: a for a in existing_actions}
    for a_data in result.get("actions", []):
        name_cn = a_data.get("name_cn","")
        if not name_cn: continue
        le  = [entity_name_to_id.get(n) for n in (a_data.get("linked_entities") or []) if entity_name_to_id.get(n)]
        li  = [rule_name_to_id.get(n)   for n in (a_data.get("linked_logic_names") or []) if rule_name_to_id.get(n)]
        if name_cn in existing_act_map:
            act = existing_act_map[name_cn]
            if a_data.get("execution_rule"): act.execution_rule = a_data["execution_rule"]
            if a_data.get("function_code"):  act.function_code  = a_data["function_code"]
            if le: act.linked_entities  = le
            if li: act.linked_logic_ids = li
            act.confidence = a_data.get("confidence", act.confidence)
        else:
            db.add(Action(id=str(uuid.uuid4()), ontology_id=oid,
                          name_cn=name_cn, name_en=a_data.get("name_en"),
                          execution_rule=a_data.get("execution_rule",""),
                          function_code=a_data.get("function_code",""),
                          description=a_data.get("description"),
                          confidence=a_data.get("confidence",0.85),
                          linked_entities=le, linked_logic_ids=li))
    db.commit()

    # ── Final counts ────────────────────────────────────────────────────────
    final_ents  = db.query(Entity).filter(Entity.ontology_id == oid).count()
    final_rules = db.query(LogicRule).filter(LogicRule.ontology_id == oid).count()
    final_acts  = db.query(Action).filter(Action.ontology_id == oid).count()
    final_rels  = db.query(Relation).filter(Relation.ontology_id == oid).count()

    # Quality metrics
    ents_list  = db.query(Entity).filter(Entity.ontology_id == oid).all()
    rules_list = db.query(LogicRule).filter(LogicRule.ontology_id == oid).all()
    acts_list  = db.query(Action).filter(Action.ontology_id == oid).all()
    with_props    = sum(1 for e in ents_list if e.properties and len(e.properties) > 0)
    with_le_rule  = sum(1 for r in rules_list if r.linked_entities and len(r.linked_entities) > 0)
    with_code     = sum(1 for a in acts_list if a.function_code and len(a.function_code.strip()) > 20)

    print(f"\n  ✅ 完成！总耗时 {t_total:.1f}s (LLM {t_llm:.1f}s + 推理 {t_infer:.1f}s)")
    return {
        "label": label,
        "duration_s": round(t_total, 1),
        "llm_s": round(t_llm, 1),
        "infer_s": round(t_infer, 1),
        "entities": final_ents,
        "with_props": with_props,
        "relations": final_rels,
        "logic_rules": final_rules,
        "with_le": with_le_rule,
        "actions": final_acts,
        "with_code": with_code,
        "files": len(files),
        "text_len": len(combined_text),
    }


def main():
    db = SessionLocal()
    model_cfg = get_deepseek_model(db)
    if not model_cfg:
        print("❌ 无模型配置，退出")
        return

    # Auto-pick model name
    model_name = model_cfg.models[0] if model_cfg.models else ""
    print(f"使用模型: {model_cfg.name} / {model_name}")

    results = []
    for (oid, pid, mid, mn, label) in TEST_CASES:
        mc = db.query(ModelConfig).filter(ModelConfig.id == mid).first() if mid else model_cfg
        mn = mn or model_name
        r = run_one(db, oid, pid, mc, mn, label)
        if r:
            results.append(r)

    # ── Summary ─────────────────────────────────────────────────────────────
    print(f"\n\n{'='*70}")
    print("  各领域本体提取汇总")
    print(f"{'='*70}")
    hdr = f"{'领域':<18} {'时长':>6} {'实体':>5} {'属性%':>6} {'关系':>5} {'规则':>5} {'动作':>5} {'代码%':>6}"
    print(hdr)
    print("-" * 70)
    for r in results:
        prop_pct  = f"{r['with_props']/r['entities']*100:.0f}%" if r['entities'] else "—"
        code_pct  = f"{r['with_code']/r['actions']*100:.0f}%"  if r['actions']  else "—"
        print(f"{r['label']:<18} {r['duration_s']:>5.1f}s {r['entities']:>5} {prop_pct:>6} "
              f"{r['relations']:>5} {r['logic_rules']:>5} {r['actions']:>5} {code_pct:>6}")
    print(f"\n提示：属性% = 有属性的实体占比，代码% = 有function_code的动作占比")


if __name__ == "__main__":
    main()
