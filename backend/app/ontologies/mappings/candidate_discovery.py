"""Generated logic and action candidate materialization for Mapping builds."""
from __future__ import annotations

import uuid as _uuid

from app.models.v2.mapping import OntologyMapping


class CandidateDiscoveryMixin:
    """Materialize deterministic v1/v2 logic and action candidates."""

    def _upsert_v2_logic(self, ontology_id: str, name: str, logic_type: str, description: str,
                         target_entity_type: str | None, expression: dict, source_type: str,
                         severity: str = "info") -> bool:
        from app.models.v2.logic import OntologyLogicRule

        # session 为 autoflush=False, 先 flush 让同一次运行中已 add 的同名规则可见
        self._db.flush()
        exists = self._db.query(OntologyLogicRule).filter(
            OntologyLogicRule.ontology_id == ontology_id,
            OntologyLogicRule.name == name,
        ).first()
        if exists:
            exists.logic_type = logic_type
            exists.description = description
            exists.target_entity_type = target_entity_type
            exists.expression = expression
            exists.source_type = source_type
            exists.severity = severity
            return False
        self._db.add(OntologyLogicRule(
            ontology_id=ontology_id,
            name=name,
            logic_type=logic_type,
            description=description,
            target_entity_type=target_entity_type,
            expression=expression,
            source_type=source_type,
            severity=severity,
            status="draft",
            enabled=True,
        ))
        return True

    @staticmethod
    def _readable_formula(logic_type: str, expr: dict | None, target: str | None = None) -> str:
        """把 logic 的结构化 expression 转成人类可读的公式串 (用于 v1 LogicRule.formula)"""
        e = expr or {}
        if logic_type == "validation":
            if e.get("missing_count") is not None:
                return f"{e.get('column')} 必填（缺失 {e.get('missing_count')} 行）"
            if e.get("properties"):
                return f"{target or ''} 字段类型契约（{len(e['properties'])} 个属性）".strip()
            op = e.get("operator") or e.get("op")
            if op:
                return f"{e.get('column') or e.get('field')} {op} {e.get('value')}"
            return f"{target or ''} 数据质量校验".strip()
        if logic_type == "state":
            states = e.get("states") or []
            shown = ", ".join(str(x) for x in states[:6])
            return f"{e.get('state_property')} ∈ {{{shown}}}"
        if logic_type == "mapping":
            return f"{target} ← curated:{str(e.get('curated_dataset_id') or '')[:8]}"
        if logic_type == "inference":
            return f"{e.get('src')} -[{e.get('rel_type')}]-> {e.get('tgt')}"
        if logic_type == "automation":
            return f"{e.get('trigger') or 'event'} ⇒ {e.get('effect') or 'action'}"
        return logic_type

    def _upsert_v1_logic(self, ontology_id: str, name: str, logic_type: str, description: str,
                         linked_entities: list[str] | None = None, confidence: float = 0.85,
                         formula: str | None = None) -> bool:
        from app.models.logic import LogicRule

        formula = formula or logic_type
        self._db.flush()
        exists = self._db.query(LogicRule).filter(
            LogicRule.ontology_id == ontology_id,
            LogicRule.name_cn == name,
        ).first()
        if exists:
            exists.description = description
            exists.formula = formula
            exists.linked_entities = linked_entities or []
            return False
        self._db.add(LogicRule(
            id=str(_uuid.uuid5(_uuid.NAMESPACE_URL, f"{ontology_id}:logic:{name}")),
            ontology_id=ontology_id,
            name_cn=name,
            name_en=name.replace(" ", "_").replace(":_", "_"),
            description=description,
            formula=formula,
            confidence=confidence,
            enabled=True,
            status="draft",
            linked_entities=linked_entities or [],
        ))
        return True

    def _discover_logic_rules(self, ontology_id: str, mappings: list[OntologyMapping],
                              mapping_meta: dict, relation_results: list[dict]) -> dict:
        created_v2 = 0
        created_v1 = 0

        for m in mappings:
            meta = mapping_meta.get(m.id)
            if not meta:
                continue
            field_map = m.field_mapping or {}
            pk_col = meta.get("pk_col")
            mapping_name = f"Mapping Rule: {m.entity_class}"
            desc = f"{m.entity_class} object type is built from curated dataset {m.curated_dataset_id}."
            expr = {
                "curated_dataset_id": m.curated_dataset_id,
                "primary_key": pk_col,
                "field_mapping": field_map,
                "property_mappings": meta.get("property_mappings", []),
                "row_count": len(meta.get("rows", [])),
            }
            created_v2 += int(self._upsert_v2_logic(
                ontology_id, mapping_name, "mapping", desc, m.entity_class, expr, "mapping", "info",
            ))
            created_v1 += int(self._upsert_v1_logic(
                ontology_id, mapping_name, "mapping", desc, [m.entity_class], 0.9,
                formula=self._readable_formula("mapping", expr, m.entity_class),
            ))

            for col in meta.get("columns", []):
                if col == "content":
                    continue
                values = [row.get(col) for row in meta.get("rows", [])]
                missing = sum(1 for v in values if v in (None, ""))
                if missing:
                    name = f"Validation Rule: {m.entity_class}.{col} completeness"
                    description = f"Validate completeness for {m.entity_class}.{col}; missing rows: {missing}."
                    created_v2 += int(self._upsert_v2_logic(
                        ontology_id, name, "validation", description, m.entity_class,
                        {"column": col, "missing_count": missing, "row_count": len(values)},
                        "schema_quality", "warning",
                    ))
                    created_v1 += int(self._upsert_v1_logic(
                        ontology_id, name, "validation", description, [m.entity_class], 0.8,
                        formula=self._readable_formula(
                            "validation", {"column": col, "missing_count": missing}, m.entity_class),
                    ))

            typed_properties = [
                {"property": item.get("property"), "column": item.get("column"), "type": item.get("type")}
                for item in meta.get("property_mappings", [])
                if isinstance(item, dict) and not item.get("hidden")
            ]
            if typed_properties:
                name = f"Schema Rule: {m.entity_class} property types"
                description = f"Schema contract for {m.entity_class} properties inferred from curated dataset columns."
                created_v2 += int(self._upsert_v2_logic(
                    ontology_id, name, "validation", description, m.entity_class,
                    {"properties": typed_properties, "primary_key": pk_col},
                    "schema", "info",
                ))
                created_v1 += int(self._upsert_v1_logic(
                    ontology_id, name, "validation", description, [m.entity_class], 0.84,
                    formula=self._readable_formula(
                        "validation", {"properties": typed_properties, "primary_key": pk_col}, m.entity_class),
                ))

            state_cols = [
                col for col in meta.get("columns", [])
                if any(token in col.lower() for token in ("status", "state")) or any(token in col for token in ("状态", "阶段"))
            ]
            for col in state_cols:
                states = sorted({str(row.get(col)) for row in meta.get("rows", []) if row.get(col) not in (None, "")})
                if states:
                    name = f"State Rule: {m.entity_class}.{col}"
                    description = f"State property discovered on {m.entity_class}.{col}: {', '.join(states[:8])}."
                    created_v2 += int(self._upsert_v2_logic(
                        ontology_id, name, "state", description, m.entity_class,
                        {"state_property": col, "states": states}, "state_detection", "info",
                    ))
                    created_v1 += int(self._upsert_v1_logic(
                        ontology_id, name, "state", description, [m.entity_class], 0.82,
                        formula=self._readable_formula(
                            "state", {"state_property": col, "states": states}, m.entity_class),
                    ))

        for rel in relation_results:
            if not rel.get("count"):
                continue
            name = f"Inference Rule: {rel.get('src')} -> {rel.get('tgt')} via {rel.get('rel_type')}"
            description = f"Infer link type {rel.get('rel_type')} from {rel.get('src')} to {rel.get('tgt')}."
            created_v2 += int(self._upsert_v2_logic(
                ontology_id, name, "inference", description, rel.get("src"),
                {"src": rel.get("src"), "tgt": rel.get("tgt"), "rel_type": rel.get("rel_type"),
                 "fk_col": rel.get("fk_col"), "src_key": rel.get("src_key"), "tgt_key": rel.get("tgt_key")},
                "relation_inference", "info",
            ))
            created_v1 += int(self._upsert_v1_logic(
                ontology_id, name, "inference", description, [rel.get("src"), rel.get("tgt")], 0.85,
                formula=self._readable_formula("inference", {
                    "src": rel.get("src"), "tgt": rel.get("tgt"), "rel_type": rel.get("rel_type")}),
            ))

        automation_name = "Automation Rule: Approved curated dataset triggers mapping sync"
        automation_desc = "When a curated dataset is approved, incremental ontology mapping can upsert objects, links, logic and actions."
        created_v2 += int(self._upsert_v2_logic(
            ontology_id, automation_name, "automation", automation_desc, None,
            {"trigger": "curated_review.approved", "effect": "mapping_resync"},
            "workflow", "info",
        ))
        created_v1 += int(self._upsert_v1_logic(
            ontology_id, automation_name, "automation", automation_desc, [], 0.86,
            formula=self._readable_formula(
                "automation", {"trigger": "curated_review.approved", "effect": "mapping_resync"}),
        ))

        self._db.flush()
        from app.models.v2.logic import OntologyLogicRule
        from app.models.logic import LogicRule
        return {
            "created_v2": created_v2,
            "created_v1": created_v1,
            "total_v2": self._db.query(OntologyLogicRule).filter(OntologyLogicRule.ontology_id == ontology_id).count(),
            "total_v1": self._db.query(LogicRule).filter(LogicRule.ontology_id == ontology_id).count(),
        }

    def _upsert_v2_action(self, ontology_id: str, name: str, category: str, description: str,
                          target_entity_type: str | None, parameters: list, effects: list,
                          criteria: list | None = None) -> bool:
        from app.models.v2.action import OntologyActionType

        self._db.flush()
        exists = self._db.query(OntologyActionType).filter(
            OntologyActionType.ontology_id == ontology_id,
            OntologyActionType.name == name,
        ).first()
        if exists:
            exists.action_category = category
            exists.description = description
            exists.target_entity_type = target_entity_type
            exists.parameters = parameters
            exists.effects = effects
            exists.submission_criteria = criteria
            return False
        self._db.add(OntologyActionType(
            ontology_id=ontology_id,
            name=name,
            action_category=category,
            description=description,
            target_entity_type=target_entity_type,
            parameters=parameters,
            submission_criteria=criteria,
            effects=effects,
            permission_rules=[{"role": "admin"}],
            status="draft",
            enabled=True,
        ))
        return True

    def _upsert_v1_action(self, ontology_id: str, name: str, category: str, description: str,
                          linked_entities: list[str] | None = None, linked_logic_ids: list[str] | None = None,
                          confidence: float = 0.82) -> bool:
        from app.models.action import Action

        self._db.flush()
        exists = self._db.query(Action).filter(
            Action.ontology_id == ontology_id,
            Action.name_cn == name,
        ).first()
        function_name = name.lower().replace(" ", "_").replace(":", "").replace("-", "_")
        function_code = (
            f"def {function_name}(context: dict) -> dict:\n"
            f"    return {{'status': 'queued', 'action': '{name}', 'context': context}}\n"
        )
        if exists:
            exists.description = description
            exists.execution_rule = category
            exists.linked_entities = linked_entities or []
            exists.linked_logic_ids = linked_logic_ids or []
            exists.function_code = function_code
            return False
        self._db.add(Action(
            id=str(_uuid.uuid5(_uuid.NAMESPACE_URL, f"{ontology_id}:action:{name}")),
            ontology_id=ontology_id,
            name_cn=name,
            name_en=function_name,
            description=description,
            execution_rule=category,
            function_code=function_code,
            linked_entities=linked_entities or [],
            linked_logic_ids=linked_logic_ids or [],
            confidence=confidence,
            enabled=True,
            status="draft",
        ))
        return True

    def _discover_action_types(self, ontology_id: str, mappings: list[OntologyMapping],
                               mapping_meta: dict,
                               relation_results: list[dict], logic_result: dict) -> dict:
        created_v2 = 0
        created_v1 = 0

        for m in mappings:
            meta = mapping_meta.get(m.id, {})
            for verb, category, effect in (
                ("Create", "crud", "create_object"),
                ("Update", "crud", "update_object"),
            ):
                name = f"{verb} {m.entity_class}"
                description = f"{verb} object records for {m.entity_class}."
                created_v2 += int(self._upsert_v2_action(
                    ontology_id, name, category, description, m.entity_class,
                    [{"name": "data", "type": "object", "required": True}],
                    [{"action": effect, "entity_type": m.entity_class}],
                    [{"logic_type": "validation", "target_entity_type": m.entity_class}],
                ))
                created_v1 += int(self._upsert_v1_action(
                    ontology_id, name, category, description, [m.entity_class], [], 0.84,
                ))

            visible_props = [
                item for item in meta.get("property_mappings", [])
                if isinstance(item, dict) and not item.get("hidden")
            ]
            state_props = [
                item for item in visible_props
                if any(token in str(item.get("column", "")).lower() for token in ("status", "state"))
                or any(token in str(item.get("column", "")) for token in ("状态", "阶段"))
            ]
            for item in state_props:
                prop = item.get("property") or item.get("column")
                name = f"Change {m.entity_class} {prop}"
                description = f"Change state property {prop} on {m.entity_class}."
                created_v2 += int(self._upsert_v2_action(
                    ontology_id, name, "state_transition", description, m.entity_class,
                    [{"name": "target_id", "type": "object_ref", "required": True},
                     {"name": str(prop), "type": "string", "required": True}],
                    [{"action": "set_property", "property": prop}],
                    [{"logic_type": "state", "target_entity_type": m.entity_class}],
                ))
                created_v1 += int(self._upsert_v1_action(
                    ontology_id, name, "state_transition", description, [m.entity_class], [], 0.82,
                ))

            timestamp_props = [
                item for item in visible_props
                if item.get("type") == "timestamp" or any(token in str(item.get("column", "")).lower() for token in ("date", "time", "_at"))
            ]
            for item in timestamp_props[:3]:
                prop = item.get("property") or item.get("column")
                name = f"Update {m.entity_class} {prop}"
                description = f"Update timestamp property {prop} on {m.entity_class}."
                created_v2 += int(self._upsert_v2_action(
                    ontology_id, name, "crud", description, m.entity_class,
                    [{"name": "target_id", "type": "object_ref", "required": True},
                     {"name": str(prop), "type": "timestamp", "required": True}],
                    [{"action": "set_property", "property": prop}],
                ))
                created_v1 += int(self._upsert_v1_action(
                    ontology_id, name, "crud", description, [m.entity_class], [], 0.8,
                ))

        seen_rel_actions: set[str] = set()
        for rel in relation_results:
            if not rel.get("count"):
                continue
            for verb, effect in (("Link", "merge_relationship"), ("Unlink", "delete_relationship")):
                name = f"{verb} {rel.get('src')} to {rel.get('tgt')}"
                if name in seen_rel_actions:
                    continue
                seen_rel_actions.add(name)
                description = f"{verb} {rel.get('rel_type')} relation between {rel.get('src')} and {rel.get('tgt')}."
                created_v2 += int(self._upsert_v2_action(
                    ontology_id, name, "link", description, rel.get("src"),
                    [{"name": "source_id", "type": "object_ref", "required": True},
                     {"name": "target_id", "type": "object_ref", "required": True}],
                    [{"action": effect, "relation_type": rel.get("rel_type")}],
                ))
                created_v1 += int(self._upsert_v1_action(
                    ontology_id, name, "link", description, [rel.get("src"), rel.get("tgt")], [], 0.83,
                ))

        for name, category, desc in (
            ("Review Curated Mapping Candidate", "review", "Review and approve generated mapping, logic and action candidates."),
            ("Repair Data Quality Issue", "repair", "Fix missing, duplicated or invalid mapped object properties."),
            ("Sync Approved Object to External System", "writeback", "Write approved object changes back to an external system."),
        ):
            created_v2 += int(self._upsert_v2_action(
                ontology_id, name, category, desc, None,
                [{"name": "target_id", "type": "string", "required": False}],
                [{"action": category}],
            ))
            created_v1 += int(self._upsert_v1_action(
                ontology_id, name, category, desc, [], [], 0.78,
            ))

        self._db.flush()
        from app.models.v2.action import OntologyActionType
        from app.models.action import Action
        return {
            "created_v2": created_v2,
            "created_v1": created_v1,
            "total_v2": self._db.query(OntologyActionType).filter(OntologyActionType.ontology_id == ontology_id).count(),
            "total_v1": self._db.query(Action).filter(Action.ontology_id == ontology_id).count(),
            "logic_total_v2": logic_result.get("total_v2", 0),
        }
