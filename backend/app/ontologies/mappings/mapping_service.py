"""Ontology Mapping 执行服务 — PRD v1.1: Entity Mapping + Relation推断 + ChromaDB写入"""
from __future__ import annotations
import logging
import uuid as _uuid
import json
import re
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from app.models.v2.mapping import OntologyMapping

logger = logging.getLogger(__name__)


class MappingSourceError(ValueError):
    """映射绑定的数据版本不可读取、为空或未满足治理闸门。"""


class MappingApplyError(RuntimeError):
    """映射写入或正规本体投影失败；调用方不得把本次运行视为 applied。"""


class MappingReleaseScopeError(MappingApplyError):
    """Mutable runtime mapping definitions do not match the current release."""


def _release_field_mapping(value: dict | None) -> dict:
    """Remove apply bookkeeping while retaining identity/projection semantics."""
    return {
        key: item for key, item in dict(value or {}).items()
        if key != "__last_apply_error__" and not key.startswith("__applied_")
    }


def load_mapping_source_rows(db: Session, mapping: OntologyMapping, *,
                             require_approved: bool = True) -> tuple[list[dict], object | None]:
    """严格全量读取映射绑定的数据版本，返回 ``(rows, DatasetVersion)``。

    正式映射不允许使用 UI preview 的容错/截断语义。对新 ``v2_datasets`` 使用
    checksum 校验的全量读取；curated 数据要求当前版本审批通过。仅为兼容旧
    ``v2_curated_datasets``，允许读取其 sample_rows/无上限 preview，读取为空
    时明确失败而不是空跑成功。
    """
    dataset_id = mapping.curated_dataset_id
    if not dataset_id:
        raise MappingSourceError(f"Mapping {mapping.id} 未绑定数据集")

    from app.models.v2.dataset import Dataset
    from app.services.v2.dataset_service import DatasetService
    from app.data_channel.curated.review_service import (
        ReviewApprovalError, latest_dataset_version, load_all_rows_with_edits)

    ds = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    version = latest_dataset_version(db, dataset_id)
    if ds is not None:
        if ds.kind == "curated":
            try:
                rows = load_all_rows_with_edits(
                    db, dataset_id, require_approved=require_approved,
                    version=version)
            except ReviewApprovalError as e:
                raise MappingSourceError(str(e)) from e
        else:
            rows = DatasetService(db).load_all_rows(
                dataset_id, version.version_no if version is not None else None)
    else:
        # 迁移前的 legacy curated 兼容；没有真实版本时无法提供版本级血缘。
        from app.models.v2.curated import CuratedDataset
        legacy = db.query(CuratedDataset).filter(CuratedDataset.id == dataset_id).first()
        if legacy is None:
            raise MappingSourceError(f"映射绑定的数据集 {dataset_id} 不存在")
        if require_approved and legacy.status != "approved":
            raise MappingSourceError(f"数据集「{legacy.name}」尚未审批通过")
        rows = list((legacy.schema_json or {}).get("sample_rows") or [])
        if not rows:
            # 无上限仅用于旧表兼容；真实 v2 Dataset 上方始终走严格 load_all_rows。
            rows = DatasetService(db).preview(dataset_id, None, limit=None)

    if not rows:
        name = getattr(ds, "name", None) or dataset_id
        raise MappingSourceError(f"映射数据集「{name}」当前版本为空，已拒绝空跑投影")
    return rows, version


class MappingService:

    def __init__(self, db: Session):
        self._db = db

    # ── CRUD ─────────────────────────────────────────────────────────

    def create_mapping(self, ontology_id: str, curated_dataset_id: str, entity_class: str,
                       field_mapping: dict, primary_key_column: str | None = None,
                       confidence: float = 1.0,
                       target_object_type_id: str | None = None) -> OntologyMapping:
        field_mapping = dict(field_mapping or {})
        if primary_key_column and "__primary_key__" not in field_mapping:
            field_mapping["__primary_key__"] = primary_key_column
            # API callers can only pass the Dataset.schema_json contract.  This
            # argument is not an independent identity definition.
            field_mapping["__pk_source__"] = "lake"
        mapping = OntologyMapping(
            ontology_id=ontology_id, curated_dataset_id=curated_dataset_id,
            entity_class=entity_class, field_mapping=field_mapping,
            target_object_type_id=target_object_type_id,
            status="draft", confidence=confidence,
        )
        self._db.add(mapping); self._db.commit(); self._db.refresh(mapping)
        return mapping

    def get_mappings(self, ontology_id: str) -> list[OntologyMapping]:
        return self._db.query(OntologyMapping).filter(OntologyMapping.ontology_id == ontology_id).all()

    def _assert_current_release_scope(
            self, ontology_id: str, mappings: list[OntologyMapping], *, project=None,
    ) -> str | None:
        """Return the immutable owner after comparing full mapping definitions.

        Legacy projects without a release pointer keep their historical behavior.
        Once a current release exists, however, mutable live mapping tables may
        materialize formal data only when they exactly match that release.
        """
        from app.models.ontology import OntologyProject
        from app.models.ontology_version import OntologyVersion
        from app.models.v2.mapping import OntologyLinkMapping
        from app.ontologies.versions.evolution_service import complete_snapshot

        if project is None:
            project = self._db.query(OntologyProject).filter(
                OntologyProject.id == ontology_id,
            ).first()
        if project is None:
            raise MappingApplyError(f"本体 {ontology_id} 不存在")
        # Some legacy/internal callers use lightweight session doubles and have
        # no release model at all.  Only a real project row can establish an
        # immutable ownership boundary.
        if not isinstance(project, OntologyProject):
            return None
        if not project.current_release_id:
            return None

        release = self._db.query(OntologyVersion).filter(
            OntologyVersion.id == project.current_release_id,
            OntologyVersion.ontology_id == ontology_id,
            OntologyVersion.node_kind == "release",
            OntologyVersion.lifecycle_status == "released",
        ).first()
        if release is None:
            raise MappingReleaseScopeError(
                "当前发布指针无效，已拒绝写入正式实例")
        snapshot = complete_snapshot(release.snapshot_formal)

        live_objects = {
            str(item.id): {
                "id": str(item.id),
                "curatedDatasetId": item.curated_dataset_id,
                "entityClass": item.entity_class,
                "fieldMapping": _release_field_mapping(item.field_mapping),
                "targetObjectTypeId": item.target_object_type_id,
                "confidence": item.confidence,
            }
            for item in mappings
        }
        released_objects = {
            str(item.get("id")): {
                "id": str(item.get("id")),
                "curatedDatasetId": item.get("curatedDatasetId"),
                "entityClass": item.get("entityClass") or "",
                "fieldMapping": _release_field_mapping(item.get("fieldMapping")),
                "targetObjectTypeId": item.get("targetObjectTypeId"),
                "confidence": item.get("confidence"),
            }
            for item in snapshot["mappings"] if item.get("id")
        }

        live_link_rows = self._db.query(OntologyLinkMapping).filter(
            OntologyLinkMapping.ontology_id == ontology_id,
        ).all()
        live_links = {
            str(item.id): {
                "id": str(item.id),
                "srcDatasetId": item.src_dataset_id,
                "tgtDatasetId": item.tgt_dataset_id,
                "relationType": item.relation_type,
                "srcKey": item.src_key,
                "tgtKey": item.tgt_key,
                "linkTypeId": item.link_type_id,
                "edgeDatasetId": item.edge_dataset_id,
                "fieldMapping": _release_field_mapping(item.field_mapping),
            }
            for item in live_link_rows
        }
        released_links = {
            str(item.get("id")): {
                "id": str(item.get("id")),
                "srcDatasetId": item.get("srcDatasetId"),
                "tgtDatasetId": item.get("tgtDatasetId"),
                "relationType": item.get("relationType") or "",
                "srcKey": item.get("srcKey") or "",
                "tgtKey": item.get("tgtKey") or "",
                "linkTypeId": item.get("linkTypeId"),
                "edgeDatasetId": item.get("edgeDatasetId"),
                "fieldMapping": _release_field_mapping(item.get("fieldMapping")),
            }
            for item in snapshot["linkMappings"] if item.get("id")
        }

        if live_objects != released_objects or live_links != released_links:
            raise MappingReleaseScopeError(
                "当前运行映射不属于当前发布快照，已拒绝写入正式实例；"
                "请在草稿中完成试跑并晋级发布")
        return str(release.id)

    def remove_mapping_projection(self, mapping: OntologyMapping) -> list[str]:
        """Remove this mapping's materialized current state in the caller transaction.

        Shared identities remain while another mapping still owns them.  Historical
        facts are retained and receive object/link tombstones.
        """
        self._adopt_legacy_projection_ownership(mapping)
        return self._reconcile_mapping_entities(mapping, set())

    def remove_link_mapping_projection(self, link_mapping) -> list[str]:
        """Remove relations/materialized Formal links owned by a LinkMapping."""
        from app.models.relation import Relation
        from app.models.v2.mapping import OntologyLinkMapping
        from app.models.ontology_formal import ObjectInstance, LinkType, LinkInstance
        from app.ontologies.formal_modeling.facts import record_link_fact

        signature = (
            OntologyLinkMapping.ontology_id == link_mapping.ontology_id,
            OntologyLinkMapping.src_dataset_id == link_mapping.src_dataset_id,
            OntologyLinkMapping.tgt_dataset_id == link_mapping.tgt_dataset_id,
            OntologyLinkMapping.relation_type == link_mapping.relation_type,
            OntologyLinkMapping.src_key == link_mapping.src_key,
            OntologyLinkMapping.tgt_key == link_mapping.tgt_key,
            OntologyLinkMapping.edge_dataset_id == link_mapping.edge_dataset_id,
        )
        possible_owners = self._db.query(OntologyLinkMapping).filter(*signature).count()
        legacy: list[Relation] = []
        for relation in self._db.query(Relation).filter(
                Relation.ontology_id == link_mapping.ontology_id,
                Relation.type == link_mapping.relation_type).all():
            props = relation.properties or {}
            if props.get("mapping_type") != "link_mapping" or props.get("__link_mapping_id__"):
                continue
            direct_match = (props.get("src_key") == link_mapping.src_key
                            and props.get("tgt_key") == link_mapping.tgt_key)
            edge_match = bool(link_mapping.edge_dataset_id and props.get("__edge_key__"))
            if direct_match or edge_match:
                legacy.append(relation)
        if legacy and possible_owners != 1:
            raise MappingApplyError(
                f"关系 {link_mapping.relation_type} 有 {len(legacy)} 条历史投影缺少 LinkMapping 血缘，"
                f"且存在 {possible_owners} 个同签名映射；拒绝猜测删除归属")
        for relation in legacy:
            props = dict(relation.properties or {})
            props["__link_mapping_id__"] = link_mapping.id
            relation.properties = props

        owned = [
            relation for relation in self._db.query(Relation).filter(
                Relation.ontology_id == link_mapping.ontology_id).all()
            if (relation.properties or {}).get("__link_mapping_id__") == link_mapping.id
        ]
        internal_relation_keys = {
            "mapping_type", "src_key", "tgt_key", "cardinality",
            "__edge_key__", "__link_mapping_id__", "fk_column",
            "alt_column", "source",
        }
        # One-time lineage adoption for links projected before
        # source_relation_id existed.  Only an exact, unambiguous endpoint/type/
        # business-property match is accepted.
        for relation in owned:
            source = self._db.query(ObjectInstance).filter(
                ObjectInstance.ontology_id == link_mapping.ontology_id,
                ObjectInstance.external_id == relation.source_entity).first()
            target = self._db.query(ObjectInstance).filter(
                ObjectInstance.ontology_id == link_mapping.ontology_id,
                ObjectInstance.external_id == relation.target_entity).first()
            if source is None or target is None:
                continue
            link_type_ids = [item.id for item in self._db.query(LinkType).filter(
                LinkType.ontology_id == link_mapping.ontology_id,
                LinkType.name == relation.type,
                LinkType.source_object_type_id == source.object_type_id,
                LinkType.target_object_type_id == target.object_type_id,
            ).all()]
            business_props = {
                key: value for key, value in dict(relation.properties or {}).items()
                if key not in internal_relation_keys
            }
            candidates = [item for item in self._db.query(LinkInstance).filter(
                LinkInstance.ontology_id == link_mapping.ontology_id,
                LinkInstance.source_object_id == source.id,
                LinkInstance.target_object_id == target.id,
                LinkInstance.source_relation_id.is_(None),
            ).all() if item.link_type_id in link_type_ids
                and dict(item.properties or {}) == business_props]
            if len(candidates) > 1:
                raise MappingApplyError(
                    f"关系 {relation.id} 对应 {len(candidates)} 条无血缘 Formal Link，拒绝猜测删除")
            if candidates:
                candidates[0].source_relation_id = relation.id
        relation_ids = [relation.id for relation in owned]
        if relation_ids:
            for link in self._db.query(LinkInstance).filter(
                    LinkInstance.ontology_id == link_mapping.ontology_id).all():
                if link.source_relation_id in relation_ids:
                    record_link_fact(
                        self._db, ontology_id=link_mapping.ontology_id,
                        link_instance_id=link.id, link_type_id=link.link_type_id,
                        exists=False, source=f"link-mapping://{link_mapping.id}")
                    self._db.delete(link)
            for relation in owned:
                self._db.delete(relation)
        self._db.flush()
        return relation_ids

    # ── 单个 Mapping 应用 ─────────────────────────────────────────────

    def apply_mapping(self, mapping_id: str, data: list[dict], *,
                      ontology_id: str | None = None,
                      source_dataset_version_id: str | None = None) -> dict:
        from app.models.ontology import OntologyProject

        q = self._db.query(OntologyMapping).filter(OntologyMapping.id == mapping_id)
        if ontology_id is not None:
            q = q.filter(OntologyMapping.ontology_id == ontology_id)
        mapping = q.first()
        if not mapping:
            suffix = f" in ontology {ontology_id}" if ontology_id else ""
            raise ValueError(f"Mapping {mapping_id} not found{suffix}")

        project = self._db.query(OntologyProject).filter(
            OntologyProject.id == mapping.ontology_id,
        ).with_for_update().first()
        ontology_release_id = self._assert_current_release_scope(
            mapping.ontology_id,
            self.get_mappings(mapping.ontology_id),
            project=project,
        )

        mapping.status = "applying"
        self._db.flush()
        from app.ontologies.sentinels.cdc import (
            CAPTURE_SUPPRESSED_KEY, SUPPRESS_KEY,
        )
        self._db.info[SUPPRESS_KEY] = True
        self._db.info[CAPTURE_SUPPRESSED_KEY] = True
        try:
            if data:
                self._normalize_mapping(mapping, data)
            entities = self._rows_to_entities(mapping, data)
            self._adopt_legacy_projection_ownership(mapping)
            v1_count = self._write_v1_entities(mapping, entities)
            stale_entity_ids = self._reconcile_mapping_entities(
                mapping, {entity["id"] for entity in entities})

            # 单映射路径同样要投影到正规本体（fo_object_instances）。该投影是
            # Mapping applied 的必需条件，不再作为可吞掉的 best-effort 尾步骤。
            from app.services.v2.mapping.formal_projection import project_to_formal_ontology
            pk_col = (mapping.field_mapping or {}).get("__primary_key__") or self._choose_pk_col(data)
            meta = {mapping.id: {
                "mapping_id": mapping.id,
                "curated_dataset_id": mapping.curated_dataset_id,
                "entity_class": mapping.entity_class, "pk_col": pk_col,
                "pk_source": (mapping.field_mapping or {}).get("__pk_source__"),
                "target_object_type_id": mapping.target_object_type_id,
                "rows": data,
                "entity_id_map": {
                    self._row_identity_value(row, pk_col):
                        self._stable_row_id(
                            mapping, row,
                            pk_col if self._has_complete_pk(row, pk_col) else None)
                    for row in data
                },
                "columns": list(data[0].keys()) if data else [],
                "property_mappings": (mapping.field_mapping or {}).get("__properties__", []),
            }}
            formal_projection = project_to_formal_ontology(
                self._db, mapping.ontology_id, meta,
                ontology_release_id=ontology_release_id)
            self._ensure_source_version_is_current(
                mapping, source_dataset_version_id)

            # Entity + Formal projection + applied lineage are one relational
            # transaction.  A mid-flight commit used to leave partial entities
            # behind when formal validation failed, even though the mapping was
            # marked failed.
            mapping.status = "applied"
            fm = dict(mapping.field_mapping or {})
            fm.pop("__last_apply_error__", None)
            if source_dataset_version_id:
                fm["__applied_dataset_version_id__"] = source_dataset_version_id
            mapping.field_mapping = fm
            self._db.commit()
        except Exception as e:
            logger.exception("apply_mapping 写入/正规本体投影失败")
            self._db.rollback()
            from app.ontologies.sentinels.cdc import discard_captured_changes
            discard_captured_changes(self._db)
            failed = self._db.query(OntologyMapping).filter(
                OntologyMapping.id == mapping_id).first()
            if failed is not None:
                failed.status = "failed"
                fm = dict(failed.field_mapping or {})
                fm["__last_apply_error__"] = str(e)[:1000]
                failed.field_mapping = fm
                self._db.commit()
            raise MappingApplyError(
                f"Mapping {mapping_id} 正规投影失败，未标记为 applied：{e}") from e

        # Neo4j is a rebuildable read projection, not the commit authority.
        # Write it only after the relational source-of-truth transaction commits;
        # failure is observable as a warning and can be repaired idempotently.
        neo4j_deleted = self._delete_neo4j_entities(
            mapping.ontology_id, stale_entity_ids)
        neo4j_count = self._write_neo4j(mapping.entity_class, entities)
        from app.ontologies.sentinels.cdc import dispatch_captured_changes
        sentinel_dispatch = dispatch_captured_changes(self._db)

        return {"mapping_id": mapping_id, "entity_class": mapping.entity_class,
                "nodes_created": neo4j_count, "v1_entities_written": v1_count,
                "formal_projection": formal_projection,
                "stale_entities_removed": len(stale_entity_ids),
                "stale_neo4j_nodes_removed": neo4j_deleted,
                "source_dataset_version_id": source_dataset_version_id,
                "sentinel_dispatch": sentinel_dispatch,
                "warnings": (["Neo4j 不可用或未写入节点，正规本体投影已完成"]
                             if entities and neo4j_count == 0 else []),
                "errors": 0, "total_rows": len(data)}

    # ── 全量构建：Entity → Relation → ChromaDB ────────────────────────

    def build_all(self, ontology_id: str, *, require_approved: bool = False) -> dict:
        """Rebuild one ontology as one relational transaction.

        The ontology project row is the serialization point shared with release
        publication.  Entity, relation, discovered rule/action and Formal writes
        either commit together or are rolled back together.  Neo4j/Chroma are
        rebuilt only from the committed relational truth afterwards.
        """
        from app.models.ontology import OntologyProject

        mapping_ids: list[str] = []
        try:
            project = self._db.query(OntologyProject).filter(
                OntologyProject.id == ontology_id,
            ).with_for_update().first()
            if project is None:
                raise MappingApplyError(f"本体 {ontology_id} 不存在")
            mappings = self.get_mappings(ontology_id)
            mapping_ids = [mapping.id for mapping in mappings]
            ontology_release_id = self._assert_current_release_scope(
                ontology_id, mappings, project=project)
            return self._build_all_transaction(
                ontology_id, mappings, require_approved=require_approved,
                ontology_release_id=ontology_release_id)
        except Exception as exc:
            self._db.rollback()
            from app.ontologies.sentinels.cdc import discard_captured_changes
            discard_captured_changes(self._db)
            if isinstance(exc, MappingReleaseScopeError):
                raise
            try:
                if not mapping_ids:
                    mapping_ids = [item[0] for item in self._db.query(
                        OntologyMapping.id).filter(
                            OntologyMapping.ontology_id == ontology_id).all()]
                for mapping_id in mapping_ids:
                    failed = self._db.query(OntologyMapping).filter(
                        OntologyMapping.id == mapping_id).first()
                    if failed is not None:
                        failed.status = "failed"
                        fm = dict(failed.field_mapping or {})
                        fm["__last_apply_error__"] = str(exc)[:1000]
                        failed.field_mapping = fm
                self._db.commit()
            except Exception:
                self._db.rollback()
                logger.exception("记录 build_all 失败状态时数据库不可用")
            if isinstance(exc, (MappingApplyError, MappingSourceError)):
                raise
            raise MappingApplyError(
                f"本体 {ontology_id} 全量映射失败，关系型投影已回滚：{exc}") from exc

    def _build_all_transaction(
            self, ontology_id: str, mappings: list[OntologyMapping], *,
            require_approved: bool = False,
            ontology_release_id: str | None = None) -> dict:
        if not mappings:
            return {"error": "no mappings configured", "ontology_id": ontology_id}

        # Formal Object/Link writes would otherwise trigger CDC immediately at
        # the first commit while mappings are deliberately fenced as
        # ``projecting``.  Capture the exact delta and release it only after all
        # derived projections succeed and every mapping becomes ``applied``.
        from app.ontologies.sentinels.cdc import (
            CAPTURE_SUPPRESSED_KEY, SUPPRESS_KEY,
        )
        self._db.info[SUPPRESS_KEY] = True
        self._db.info[CAPTURE_SUPPRESSED_KEY] = True

        from app.models.v2.dataset import Dataset
        from app.models.v2.curated import CuratedDataset

        # Phase 1: Entity Mapping
        entity_results = []
        mapping_meta: dict[str, dict] = {}

        for m in mappings:
            if not m.curated_dataset_id:
                continue
            rows, source_version = load_mapping_source_rows(
                self._db, m, require_approved=require_approved)

            if rows:
                self._normalize_mapping(m, rows)

            entities = self._rows_to_entities(m, rows)
            self._adopt_legacy_projection_ownership(m)
            v1_count = self._write_v1_entities(m, entities)
            stale_ids = self._reconcile_mapping_entities(
                m, {entity["id"] for entity in entities})

            pk_col = (m.field_mapping or {}).get("__primary_key__") or self._choose_pk_col(rows)
            entity_id_map = {
                self._row_identity_value(row, pk_col): self._stable_row_id(
                    m, row, pk_col if self._has_complete_pk(row, pk_col) else None)
                for row in rows
            }
            dataset_name = None
            ds = self._db.query(Dataset).filter(Dataset.id == m.curated_dataset_id).first()
            if ds:
                dataset_name = ds.name
            else:
                cd = self._db.query(CuratedDataset).filter(CuratedDataset.id == m.curated_dataset_id).first()
                if cd:
                    dataset_name = cd.name
            mapping_meta[m.id] = {
                "mapping_id": m.id,
                "curated_dataset_id": m.curated_dataset_id,
                "source_dataset_version_id": getattr(source_version, "id", None),
                "dataset_name": dataset_name,
                "entity_class": m.entity_class, "pk_col": pk_col,
                "pk_source": (m.field_mapping or {}).get("__pk_source__"),
                "target_object_type_id": m.target_object_type_id,
                "rows": rows, "entity_id_map": entity_id_map,
                "columns": list(rows[0].keys()) if rows else [],
                "property_mappings": (m.field_mapping or {}).get("__properties__", []),
            }
            m.status = "applying"
            entity_results.append({"mapping_id": m.id, "entity_class": m.entity_class,
                                   "v1_entities_written": v1_count,
                                   "stale_entities_removed": len(stale_ids),
                                   "nodes_created": 0})

        self._db.flush()

        # Phase 2: Relation 推断
        relation_results = self._infer_and_write_relations(ontology_id, mappings, mapping_meta)

        # Phase 2b: Link Mapping 处理（手动配置的跨表关系）
        link_results = self._process_link_mappings(ontology_id, mapping_meta)
        relation_results.extend(link_results)

        # Phase 3: Logic / Action Discovery
        logic_result = self._discover_logic_rules(ontology_id, mappings, mapping_meta, relation_results)
        action_result = self._discover_action_types(ontology_id, mappings, mapping_meta, relation_results, logic_result)

        # Chroma/Neo4j are derived projections.  They are rebuilt only after the
        # relational + Formal transaction succeeds below, never mid-transaction.

        # Phase 5: 投影到正规本体 (Projection to Formal Ontology)
        # 把已落地的 Entity / Relation 投影成 ObjectType / ObjectInstance /
        # LinkType / LinkInstance，让流水线数据直接在图谱编辑器里可见、可建模。
        from app.services.v2.mapping.formal_projection import project_to_formal_ontology
        formal_projection = project_to_formal_ontology(
            self._db, ontology_id, mapping_meta,
            ontology_release_id=ontology_release_id)
        for mapping_id, meta in mapping_meta.items():
            source_mapping = next(m for m in mappings if m.id == mapping_id)
            self._ensure_source_version_is_current(
                source_mapping, meta.get("source_dataset_version_id"))

        for mapping_id in mapping_meta:
            applied = self._db.query(OntologyMapping).filter(
                OntologyMapping.id == mapping_id).first()
            if applied is not None:
                # Persist a fence before touching non-transactional projections.
                # Runtime actions and publication reject this transient state.
                applied.status = "projecting"
                fm = dict(applied.field_mapping or {})
                fm.pop("__last_apply_error__", None)
                source_version_id = mapping_meta[mapping_id].get(
                    "source_dataset_version_id")
                if source_version_id:
                    fm["__applied_dataset_version_id__"] = source_version_id
                applied.field_mapping = fm
        self._db.commit()
        neo4j_rebuilt = self._rebuild_neo4j_projection(ontology_id)
        chroma_result = self._rebuild_chroma_projection(ontology_id)
        chroma_count = chroma_result if chroma_result is not None else 0
        from app.config import settings
        projection_errors = []
        if not neo4j_rebuilt:
            projection_errors.append("Neo4j projection rebuild failed")
        if chroma_result is None:
            projection_errors.append("Chroma projection rebuild failed")
        if projection_errors and settings.environment == "production":
            message = "; ".join(projection_errors)
            for mapping_id in mapping_meta:
                failed = self._db.query(OntologyMapping).filter(
                    OntologyMapping.id == mapping_id).first()
                if failed is not None:
                    failed.status = "failed"
                    fm = dict(failed.field_mapping or {})
                    fm["__last_apply_error__"] = message
                    failed.field_mapping = fm
            self._db.commit()
            raise MappingApplyError(
                "关系型/Formal 投影已提交，但派生查询投影未完成；"
                f"已阻断发布和动作执行，重试本体全量对账即可修复: {message}")

        for mapping_id in mapping_meta:
            applied = self._db.query(OntologyMapping).filter(
                OntologyMapping.id == mapping_id).first()
            if applied is not None:
                applied.status = "applied"
        self._db.commit()
        from app.ontologies.sentinels.cdc import dispatch_captured_changes
        sentinel_dispatch = dispatch_captured_changes(self._db)
        if neo4j_rebuilt:
            for result in entity_results:
                result["nodes_created"] = result["v1_entities_written"]

        return {
            "ontology_id": ontology_id,
            "entity_mappings": entity_results,
            "relations_written": relation_results,
            "logic_discovery": logic_result,
            "action_discovery": action_result,
            "formal_projection": formal_projection,
            "chroma_entities_written": chroma_count,
            "neo4j_projection_rebuilt": neo4j_rebuilt,
            "projection_warnings": projection_errors,
            "sentinel_dispatch": sentinel_dispatch,
            "total_entities": sum(r.get("v1_entities_written", 0) for r in entity_results),
            "total_relations": sum(r.get("count", 0) for r in relation_results),
            "total_logic": logic_result.get("total_v2", 0),
            "total_actions": action_result.get("total_v2", 0),
            "review_required": True,
            "publish_status": "draft",
        }

    def _ensure_source_version_is_current(
            self, mapping: OntologyMapping, source_dataset_version_id: str | None) -> None:
        """Apply 结束前重验版本，封住“读取 v1 时并发发布 v2”的竞态。"""
        if not source_dataset_version_id or not mapping.curated_dataset_id:
            return  # legacy 数据源没有 DatasetVersion，只能维持兼容语义
        from app.data_channel.curated.review_service import latest_dataset_version
        current = latest_dataset_version(self._db, mapping.curated_dataset_id)
        if current is None or current.id != source_dataset_version_id:
            current_label = f"v{current.version_no}" if current is not None else "无版本"
            raise MappingApplyError(
                f"映射执行期间数据源已更新为 {current_label}；"
                f"本次读取版本 {source_dataset_version_id} 已过期，拒绝标记 applied")

    # ── Relation 推断 ───────────────────────────────────────────────

    def _infer_and_write_relations(self, ontology_id: str, mappings: list[OntologyMapping],
                                   mapping_meta: dict) -> list[dict]:
        from app.models.entity import Entity
        from app.models.relation import Relation
        from app.models.v2.mapping import OntologyLinkMapping
        import re

        results = []
        m_list = [m for m in mappings if m.id in mapping_meta]

        # 重建前清除旧的 FK 推断关系，避免改名/数据变化后产生重复边
        stale = self._db.query(Relation).filter(Relation.ontology_id == ontology_id).all()
        for rel in stale:
            if (rel.properties or {}).get("source") == "fk_inference":
                self._db.delete(rel)
        self._db.flush()

        for i, src_m in enumerate(m_list):
            src_meta = mapping_meta[src_m.id]
            src_cols = src_meta["columns"]

            for tgt_m in m_list:
                if tgt_m.id == src_m.id:
                    continue
                tgt_meta = mapping_meta[tgt_m.id]
                tgt_class = tgt_meta["entity_class"]
                tgt_pk_col = tgt_meta["pk_col"]
                tgt_id_map = tgt_meta["entity_id_map"]

                tgt_pk_values = {
                    str(row.get(tgt_pk_col, "")).strip()
                    for row in tgt_meta.get("rows", [])
                    if row.get(tgt_pk_col) not in (None, "")
                }
                # 归一化索引: 容错 ID 格式差异 (如 SUP-001 vs SUP001)
                tgt_norm_index = {
                    self._normalize_fk_value(v): v for v in tgt_pk_values
                }
                fk_candidates = self._detect_fk_columns(
                    src_cols, tgt_class, tgt_m.entity_class,
                    src_sample_rows=src_meta.get("rows", []),
                    tgt_pk_values=tgt_pk_values,
                )

                fk_cols_linked: set[str] = set()
                for fk_col, rel_type in fk_candidates:
                    written = 0
                    src_values: list[str] = []
                    tgt_values: list[str] = []
                    seen_pairs: set[tuple[str, str]] = set()
                    for row in src_meta["rows"]:
                        fk_val = str(row.get(fk_col, ""))
                        if not fk_val:
                            continue
                        src_pk_col = src_meta["pk_col"]
                        src_pk_val = self._row_identity_value(row, src_pk_col)
                        src_eid = src_meta["entity_id_map"].get(src_pk_val)
                        tgt_eid = tgt_id_map.get(self._lookup_identity_value(tgt_pk_col, fk_val))
                        if not tgt_eid:
                            # 归一化匹配: 容错 SUP-001 vs SUP001 等格式差异
                            raw = tgt_norm_index.get(self._normalize_fk_value(fk_val))
                            if raw is not None:
                                tgt_eid = tgt_id_map.get(self._lookup_identity_value(tgt_pk_col, raw))
                        if not src_eid or not tgt_eid:
                            continue
                        # 多行映射同一实体对时去重 (如订单按 items 展开的多行)
                        if (src_eid, tgt_eid) in seen_pairs:
                            continue
                        seen_pairs.add((src_eid, tgt_eid))
                        src_exists = self._db.query(Entity).filter(Entity.id == src_eid).first()
                        tgt_exists = self._db.query(Entity).filter(Entity.id == tgt_eid).first()
                        if not src_exists or not tgt_exists:
                            continue
                        rel = Relation(
                            id=self._stable_relation_id(ontology_id, src_eid, tgt_eid, rel_type, "fk_inference"),
                            ontology_id=ontology_id,
                            source_entity=src_eid, target_entity=tgt_eid,
                            type=rel_type, properties={"fk_column": fk_col, "source": "fk_inference"},
                            confidence=0.85,
                        )
                        self._db.merge(rel)
                        src_values.append(src_eid)
                        tgt_values.append(tgt_eid)
                        written += 1
                    if written:
                        fk_cols_linked.add(fk_col)
                        cardinality = self._infer_cardinality(src_values, tgt_values)
                        inferred_link = self._upsert_inferred_link_mapping(
                            ontology_id=ontology_id,
                            src_dataset_id=src_m.curated_dataset_id,
                            tgt_dataset_id=tgt_m.curated_dataset_id,
                            relation_type=rel_type,
                            src_key=fk_col,
                            tgt_key=tgt_pk_col,
                            model=OntologyLinkMapping,
                        )
                        for rel in self._db.query(Relation).filter(
                            Relation.ontology_id == ontology_id,
                            Relation.type == rel_type,
                        ).all():
                            props = dict(rel.properties or {})
                            if props.get("source") == "fk_inference" and props.get("fk_column") == fk_col:
                                props["cardinality"] = cardinality
                                rel.properties = props
                                # JSON 列原地替换 dict 时显式标记 dirty，确保持久化
                                flag_modified(rel, "properties")
                        self._db.flush()
                        results.append({"src": src_meta["entity_class"], "tgt": tgt_class,
                                        "rel_type": rel_type, "fk_col": fk_col, "count": written,
                                        "cardinality": cardinality,
                                        "link_mapping_id": inferred_link.id if inferred_link else None,
                                        "link_mapping_status": inferred_link.status if inferred_link else None})

                # ── 策略 5: 跨表同名引用列匹配 ───────────────────────────────────
                # 当两张表有完全同名的列（如中文的"患者ID"、"订单号"），且该列看起来像
                # 引用列时，直接用该列做值关联（不依赖 PK 列名一致）。
                _ref_keywords = {"id", "编号", "编码", "号", "单号", "订单"}
                _meta_extraction_cols = {"record_id", "row_type", "rule_index", "rule_count",
                    "section_index", "section_title", "section_text", "table_index",
                    "table_row_index", "thresholds", "doc_summary", "sections",
                    "condition", "action", "name", "qualifier", "definition",
                    "extraction_method", "extraction_strategy", "extraction_error"}
                _same_name_candidates = []
                for col in src_cols:
                    if col in fk_cols_linked or col == src_meta.get("pk_col"):
                        continue
                    if col not in tgt_meta["columns"]:
                        continue
                    if col.lower() in _meta_extraction_cols:
                        continue
                    col_lower = col.lower()
                    # 列名包含引用关键词 或 列值匹配 ID 模式
                    is_ref_name = any(k in col_lower for k in _ref_keywords)
                    if not is_ref_name:
                        src_vals = {str(r.get(col, "")).strip() for r in src_meta.get("rows", []) if r.get(col)}
                        src_vals.discard("")
                        if len(src_vals) >= 2 and all(
                            re.match(r'^[A-Za-z]+[\d_\-]+\d+$', v) for v in src_vals if len(v) > 1
                        ):
                            is_ref_name = True
                    if is_ref_name:
                        _same_name_candidates.append(col)

                if _same_name_candidates:
                    tgt_rows = tgt_meta.get("rows", [])
                    tgt_id_map = tgt_meta["entity_id_map"]
                    tgt_pk_col_for_lookup = tgt_meta["pk_col"]
                    for col in _same_name_candidates:
                        # 用该列自身做 linkage（值来源列而非PK列）
                        tgt_val_to_eid: dict[str, str] = {}
                        for tgt_row in tgt_rows:
                            v = str(tgt_row.get(col, "")).strip()
                            if not v:
                                continue
                            norm_v = self._normalize_fk_value(v)
                            tgt_pk_val = self._row_identity_value(tgt_row, tgt_pk_col_for_lookup)
                            eid = tgt_id_map.get(tgt_pk_val)
                            if eid and norm_v:
                                tgt_val_to_eid[norm_v] = eid

                        if len(tgt_val_to_eid) < 2:
                            continue

                        _GENERIC_REL_NAMES = {"ID", "CODE", "NO", "NUM", "NUMBER", "NAME", "KEY", "REF", "TYPE"}
                        rel_name = re.sub(r'[^A-Za-z0-9_]', '', col.replace(" ", "_")).upper() or "REF"
                        if not rel_name or rel_name in _GENERIC_REL_NAMES:
                            # Column name was Chinese-only or too generic after stripping; derive from target entity class
                            _ec = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', tgt_class).upper()
                            rel_name = re.sub(r'[^A-Z0-9_]', '', _ec) or "REF"
                        rel_type = f"HAS_{rel_name}"
                        written = 0
                        src_values: list[str] = []
                        tgt_values: list[str] = []
                        seen_pairs: set[tuple[str, str]] = set()

                        for src_row in src_meta.get("rows", []):
                            v = str(src_row.get(col, "")).strip()
                            if not v:
                                continue
                            src_pk_val = self._row_identity_value(src_row, src_meta["pk_col"])
                            src_eid = src_meta["entity_id_map"].get(src_pk_val)
                            if not src_eid:
                                continue
                            tgt_eid = tgt_val_to_eid.get(self._normalize_fk_value(v))
                            if not tgt_eid:
                                continue
                            if (src_eid, tgt_eid) in seen_pairs:
                                continue
                            seen_pairs.add((src_eid, tgt_eid))
                            if not self._db.query(Entity).filter(Entity.id == src_eid).first():
                                continue
                            if not self._db.query(Entity).filter(Entity.id == tgt_eid).first():
                                continue
                            rel = Relation(
                                id=self._stable_relation_id(ontology_id, src_eid, tgt_eid, rel_type, "same_name_fk"),
                                ontology_id=ontology_id,
                                source_entity=src_eid, target_entity=tgt_eid,
                                type=rel_type,
                                properties={"fk_column": col, "source": "same_name_fk"},
                                confidence=0.8,
                            )
                            self._db.merge(rel)
                            src_values.append(src_eid)
                            tgt_values.append(tgt_eid)
                            written += 1

                        if written:
                            fk_cols_linked.add(col)
                            cardinality = self._infer_cardinality(src_values, tgt_values)
                            # 把基数回写进 Relation.properties，供正规本体投影使用
                            for rel in self._db.query(Relation).filter(
                                Relation.ontology_id == ontology_id,
                                Relation.type == rel_type,
                            ).all():
                                props = dict(rel.properties or {})
                                if props.get("source") == "same_name_fk" and props.get("fk_column") == col:
                                    props["cardinality"] = cardinality
                                    rel.properties = props
                                    flag_modified(rel, "properties")
                            self._db.flush()
                            results.append({"src": src_meta["entity_class"], "tgt": tgt_class,
                                            "rel_type": rel_type, "fk_col": col,
                                            "via": "same_name_fk", "count": written,
                                            "cardinality": cardinality})

                # 跨数据集备用键推断 (PRD §2.4 ③): 只跳过 FK 已实际产出链接的列,
                # 列名疑似 FK 但值匹配不上主键的列仍可走备用键(如 mentioned_supplier 存公司名)
                results.extend(self._infer_alt_key_relations(
                    ontology_id, src_m, src_meta, tgt_m, tgt_meta,
                    skip_src_cols=fk_cols_linked, link_model=OntologyLinkMapping,
                ))
        return results

    # ── 跨数据集备用键推断 (PRD §2.4 ③ "跨数据集关系推断") ──────────────

    def _detect_alt_key_columns(self, rows: list[dict], pk_col: str | None) -> list[str]:
        """检测备用键: 近唯一(≥90%)、非纯数字、值长度足够的非主键列 (如 供应商名称)"""
        import re
        if not rows:
            return []
        alt: list[str] = []
        # 小样本中 status/type 等枚举很容易“碰巧唯一”（两行 approved/pending），
        # 但它们不是实体身份键；拿它们跨表连边会生成语义错误且违反基数。
        non_identity_tokens = {
            "status", "state", "type", "category", "level", "rating", "stage"}
        non_identity_cjk = {"状态", "类型", "类别", "分类", "等级", "评级", "阶段", "标志", "是否"}
        for col in rows[0].keys():
            if col == pk_col:
                continue
            normalized_col = str(col).strip().lower().replace("_", " ")
            col_tokens = set(re.findall(r"[a-z0-9]+", normalized_col))
            if (col_tokens & non_identity_tokens
                    or any(token in normalized_col for token in non_identity_cjk)):
                continue
            vals = [str(r.get(col, "") or "").strip() for r in rows]
            vals = [v for v in vals if v]
            if len(vals) < 2:
                continue
            if all(re.fullmatch(r"[\d\s\.,%\-]+", v) for v in vals):
                continue  # 纯数字列(金额/比率)易误连
            if sum(len(v) >= 3 for v in vals) / len(vals) < 0.8:
                continue  # 短值枚举列(等级/状态)
            if len(set(vals)) / len(vals) < 0.9:
                continue
            alt.append(col)
        return alt

    def _infer_alt_key_relations(self, ontology_id: str, src_m: OntologyMapping, src_meta: dict,
                                 tgt_m: OntologyMapping, tgt_meta: dict,
                                 skip_src_cols: set[str], link_model) -> list[dict]:
        """源列值(支持逗号等分隔的多值)与目标备用键值重叠 → 推断关系。

        典型场景: 文档记录的 organizations 字段命中 Supplier.供应商名称。
        """
        import re
        from app.models.entity import Entity
        from app.models.relation import Relation

        tgt_rows = tgt_meta.get("rows", [])
        tgt_pk_col = tgt_meta["pk_col"]
        tgt_id_map = tgt_meta["entity_id_map"]
        tgt_class = tgt_meta["entity_class"]
        results: list[dict] = []

        # 跳过文档级元数据列与常量列: 全行同值的列不携带行级链接信息,
        # 否则文档摘要里提到的公司会让该文档所有行都连过去
        meta_cols = {"doc_summary", "sections", "source_file", "filename",
                     "markdown_text", "document_text", "extraction_method"}
        src_rows = src_meta.get("rows", [])
        usable_src_cols = []
        for col in src_meta["columns"]:
            if col in meta_cols or col == src_meta["pk_col"]:
                continue
            vals = {str(r.get(col, "") or "").strip() for r in src_rows}
            vals.discard("")
            if len(vals) <= 1:
                continue
            usable_src_cols.append(col)

        rel_name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", tgt_class).upper()
        rel_name = re.sub(r"[^A-Z0-9_]", "", rel_name) or "REF"
        rel_type = f"HAS_{rel_name}"

        for alt_col in self._detect_alt_key_columns(tgt_rows, tgt_pk_col):
            alt_to_eid: dict[str, str] = {}
            for row in tgt_rows:
                v = str(row.get(alt_col, "") or "").strip()
                if not v:
                    continue
                eid = tgt_id_map.get(self._row_identity_value(row, tgt_pk_col))
                if eid:
                    alt_to_eid[self._normalize_fk_value(v)] = eid
            if len(alt_to_eid) < 2:
                continue

            col_pairs: dict[str, set[tuple[str, str]]] = {}
            for row in src_meta["rows"]:
                src_eid = src_meta["entity_id_map"].get(
                    self._row_identity_value(row, src_meta["pk_col"]))
                if not src_eid:
                    continue
                for col in usable_src_cols:
                    if col in skip_src_cols:
                        continue
                    raw = str(row.get(col, "") or "").strip()
                    if not raw:
                        continue
                    parts = [p.strip() for p in re.split(r"[,，、;；|]", raw) if p.strip()]
                    for part in parts:
                        norm = self._normalize_fk_value(part)
                        if len(norm) < 3:
                            continue
                        tgt_eid = alt_to_eid.get(norm)
                        if tgt_eid:
                            col_pairs.setdefault(col, set()).add((src_eid, tgt_eid))

            for col, pairs in col_pairs.items():
                if not pairs:
                    continue  # 全名精确匹配(归一化≥3字符)误连概率低, 单行命中即视为有效
                written = 0
                src_values, tgt_values = [], []
                for src_eid, tgt_eid in pairs:
                    if not self._db.query(Entity).filter(Entity.id == src_eid).first():
                        continue
                    if not self._db.query(Entity).filter(Entity.id == tgt_eid).first():
                        continue
                    rel = Relation(
                        id=self._stable_relation_id(ontology_id, src_eid, tgt_eid, rel_type, "fk_inference"),
                        ontology_id=ontology_id,
                        source_entity=src_eid, target_entity=tgt_eid,
                        type=rel_type,
                        properties={"fk_column": col, "alt_column": alt_col,
                                    "via": "alternate_key", "source": "fk_inference"},
                        confidence=0.75,
                    )
                    self._db.merge(rel)
                    src_values.append(src_eid)
                    tgt_values.append(tgt_eid)
                    written += 1
                if not written:
                    continue
                cardinality = self._infer_cardinality(src_values, tgt_values)
                inferred_link = self._upsert_inferred_link_mapping(
                    ontology_id=ontology_id,
                    src_dataset_id=src_m.curated_dataset_id,
                    tgt_dataset_id=tgt_m.curated_dataset_id,
                    relation_type=rel_type,
                    src_key=col,
                    tgt_key=alt_col,
                    model=link_model,
                )
                self._db.flush()
                results.append({"src": src_meta["entity_class"], "tgt": tgt_class,
                                "rel_type": rel_type, "fk_col": col, "alt_col": alt_col,
                                "via": "alternate_key", "count": written,
                                "cardinality": cardinality,
                                "link_mapping_id": inferred_link.id if inferred_link else None,
                                "link_mapping_status": inferred_link.status if inferred_link else None})
        return results

    def _upsert_inferred_link_mapping(
        self,
        ontology_id: str,
        src_dataset_id: str | None,
        tgt_dataset_id: str | None,
        relation_type: str,
        src_key: str,
        tgt_key: str,
        model,
    ):
        if not src_dataset_id or not tgt_dataset_id:
            return None
        existing = self._db.query(model).filter(
            model.ontology_id == ontology_id,
            model.src_dataset_id == src_dataset_id,
            model.tgt_dataset_id == tgt_dataset_id,
            model.relation_type == relation_type,
            model.src_key == src_key,
            model.tgt_key == tgt_key,
        ).first()
        if existing:
            if existing.status not in ("active", "inferred"):
                existing.status = "inferred"
            return existing
        link = model(
            ontology_id=ontology_id,
            src_dataset_id=src_dataset_id,
            tgt_dataset_id=tgt_dataset_id,
            relation_type=relation_type,
            src_key=src_key,
            tgt_key=tgt_key,
            status="inferred",
        )
        self._db.add(link)
        return link

    # ── 工具方法 ─────────────────────────────────────────────────────

    def _normalize_mapping(self, mapping: OntologyMapping, rows: list[dict]) -> None:
        if not rows:
            return
        sample = rows[0]
        field_map = dict(mapping.field_mapping or {})
        ignored_fields = {
            str(item) for item in (field_map.get("__ignored_fields__") or [])
        }
        targets = [
            str(value) for key, value in field_map.items()
            if not str(key).startswith("__") and str(key) not in ignored_fields
        ]
        duplicate_targets = sorted({
            value for value in targets if targets.count(value) > 1
        })
        if duplicate_targets:
            raise MappingSourceError(
                "历史字段映射存在多个源列写入同一目标属性，已阻断静默覆盖："
                + "、".join(duplicate_targets))
        for col in sample.keys():
            if col == "content":
                continue
            if col not in field_map and col not in ignored_fields:
                field_map[col] = col
        # Entity.id/name/source_id are runtime-owned fields.  Mapping a business
        # column onto one of them used to overwrite the value and then made the
        # Formal layer guess another primary key.  Preserve the business value
        # under an explicit, non-reserved property instead.
        reserved_outputs = {
            "id", "ontology_id", "source_id", "object_type",
            "source_row_count", "name", "name_cn", "name_en",
            "display_name", "__mapping_ids__",
        }
        declared_target_properties: set[str] = set()
        if mapping.target_object_type_id:
            from app.models.ontology_formal import ObjectType
            target_type = self._db.query(ObjectType).filter(
                ObjectType.id == mapping.target_object_type_id,
                ObjectType.ontology_id == mapping.ontology_id,
            ).first()
            declared_target_properties = {
                str(item.get("name"))
                for item in (getattr(target_type, "properties", None) or [])
                if isinstance(item, dict) and item.get("name")
            }
        occupied = {
            str(prop) for source, prop in field_map.items()
            if not str(source).startswith("__") and str(prop) not in reserved_outputs
        }
        for col in sample.keys():
            prop = str(field_map.get(col) or col)
            if prop not in reserved_outputs or prop in declared_target_properties:
                continue
            candidate = "business_id" if prop == "id" else f"business_{prop}"
            if candidate in occupied:
                candidate = f"source_{re.sub(r'[^A-Za-z0-9_]', '_', str(col))}_value"
            field_map[col] = candidate
            occupied.add(candidate)
        canonical_dataset, declared = self._dataset_primary_key(
            mapping.curated_dataset_id)
        if canonical_dataset:
            if not declared:
                raise MappingSourceError(
                    f"映射数据集 {mapping.curated_dataset_id} 尚未声明资产湖主键契约")
            missing = [col for col in self._pk_columns(declared) if col not in sample]
            if missing:
                raise MappingSourceError(
                    f"映射数据缺少资产湖主键列：{', '.join(missing)}")
            if not self._is_unique_key(rows, declared):
                raise MappingSourceError(
                    f"映射数据违反资产湖主键 {declared}：存在空值或重复值")
            # Canonical datasets have exactly one identity authority.  Repair
            # historical mappings that once carried an independently selected key.
            field_map["__primary_key__"] = declared
            field_map["__pk_source__"] = "lake"

        pk_col = field_map.get("__primary_key__")
        order_pk_can_merge = (
            not canonical_dataset
            and bool(pk_col)
            and pk_col != "__row_hash__"
            and all(col in sample for col in self._pk_columns(pk_col))
            and ("order" in (mapping.entity_class or "").lower() or "订单" in str(mapping.entity_class or ""))
            and all(self._has_complete_pk(row, pk_col) for row in rows)
        )
        if (
            not canonical_dataset
            and (
            not pk_col
            or (
                pk_col != "__row_hash__"
                and not order_pk_can_merge
                and (not all(col in sample for col in self._pk_columns(pk_col))
                     or not self._is_unique_key(rows, pk_col))
            )
            )
        ):
            # 主键优先级：湖中声明的契约（入湖闸门校验过存在/非空/唯一）
            # > 按本次数据启发式猜测。声明来源打进 __pk_source__ 供下游区分
            field_map["__primary_key__"] = declared or self._choose_pk_col(rows)
            field_map["__pk_source__"] = "lake" if declared else "inferred"
        field_map["__properties__"] = self._property_metadata(rows, field_map)
        if field_map != (mapping.field_mapping or {}):
            mapping.field_mapping = field_map
            self._db.flush()

    def _property_metadata(self, rows: list[dict], field_map: dict) -> list[dict]:
        if not rows:
            return []
        sample = rows[0]
        existing = {
            item.get("column"): item
            for item in field_map.get("__properties__", [])
            if isinstance(item, dict) and item.get("column")
        }
        technical_cols = {
            "content", "storage_uri", "markdown_text", "structured_extraction_error",
            "structured_extraction_ok", "extraction_strategy", "extraction_method",
            "source_dataset_id",
        }
        result = []
        pk_columns = self._pk_columns(field_map.get("__primary_key__"))
        ignored_fields = {
            str(item) for item in (field_map.get("__ignored_fields__") or [])
        }
        for col in sample.keys():
            if col == "content":
                continue
            if col in ignored_fields and col not in pk_columns:
                continue
            current = dict(existing.get(col) or {})
            pk_part = pk_columns.index(col) + 1 if col in pk_columns else None
            result.append({
                "column": col,
                "property": field_map.get(col, col),
                "type": current.get("type") or self._infer_property_type(rows, col),
                # Identity fields must remain projected and inspectable.  Hiding a
                # PK component would make the Formal type unable to represent its
                # own canonical identity contract.
                "hidden": False if pk_part else bool(
                    current.get("hidden", col in technical_cols or col.startswith("__"))),
                "technical": bool(current.get("technical", col in technical_cols or col.startswith("__"))),
                "primaryKeyPart": pk_part,
                "confidence": float(current.get("confidence", 0.85)),
                "description": current.get("description", ""),
            })
        return result

    def _property_metadata_by_column(self, field_map: dict) -> dict:
        return {
            item.get("column"): item
            for item in (field_map or {}).get("__properties__", [])
            if isinstance(item, dict) and item.get("column")
        }

    def _infer_property_type(self, rows: list[dict], col: str) -> str:
        from app.services.v2.pipeline.steps.schema_inference import SchemaInferenceStep

        for row in rows[:20]:
            value = row.get(col)
            if value not in (None, ""):
                return SchemaInferenceStep._infer_type(str(value).strip())
        return "string"

    def _dataset_primary_key(self, dataset_id: str | None) -> tuple[bool, str | None]:
        """Return ``(is_canonical_dataset, canonical_pk_spec)``.

        ``isinstance`` deliberately excludes permissive MagicMock/legacy rows:
        only v2_datasets is the contract authority.
        """
        if not dataset_id:
            return False, None
        from app.data_channel.datasets.lake_gate import split_pk
        from app.models.v2.dataset import Dataset

        dataset = self._db.query(Dataset).filter(Dataset.id == dataset_id).first()
        if not isinstance(dataset, Dataset):
            return False, None
        schema = dataset.schema_json if isinstance(dataset.schema_json, dict) else {}
        columns = split_pk(schema.get("primary_key"))
        return True, ",".join(columns) or None

    def _declared_pk_col(self, dataset_id: str | None, rows: list[dict]) -> str | None:
        """资产湖中该数据集声明的主键列（schema_json.primary_key，入湖闸门维护）。

        实例身份优先采用湖中契约而非按本次加载的数据猜测——启发式对数据
        敏感（今天唯一的列明天未必），主键一漂移就是一整批实例身份作废。
        复合主键保留规范化后的逗号分隔形式，由 identity 编码为稳定 JSON。
        """
        canonical, pk = self._dataset_primary_key(dataset_id)
        if not canonical or not pk:
            return None
        if rows:
            missing = [col for col in self._pk_columns(pk) if col not in rows[0]]
            if missing:
                raise MappingSourceError(
                    f"数据集 {dataset_id} 声明的主键列不在当前数据中：{', '.join(missing)}")
        return pk

    def _choose_pk_col(self, rows: list[dict]) -> str:
        if not rows:
            return "id"
        cols = [c for c in rows[0].keys() if c != "content"]

        id_like_cols = []
        for col in cols:
            lower = col.lower()
            if lower == "id" or lower.endswith("_id") or col.endswith("ID") or "id" in lower:
                id_like_cols.append(col)
        for col in id_like_cols:
            if self._is_unique_col(rows, col):
                return col
        for preferred in ("filename", "source_file", "source_dataset_id", "order_id", "订单号", "供应商ID"):
            if preferred in cols and self._is_unique_col(rows, preferred):
                return preferred
        for col in cols:
            if self._is_unique_col(rows, col):
                return col
        return "__row_hash__"

    def _is_unique_col(self, rows: list[dict], col: str) -> bool:
        values = [str(row.get(col, "")).strip() for row in rows]
        return bool(values) and all(values) and len(set(values)) == len(values)

    @staticmethod
    def _pk_columns(pk_col: str | None) -> list[str]:
        if not pk_col or pk_col == "__row_hash__":
            return []
        return [part.strip() for part in str(pk_col).split(",") if part.strip()]

    def _has_complete_pk(self, row: dict, pk_col: str | None) -> bool:
        columns = self._pk_columns(pk_col)
        return bool(columns) and all(
            col in row and self._has_display_value(row.get(col)) for col in columns)

    def _is_unique_key(self, rows: list[dict], pk_col: str) -> bool:
        columns = self._pk_columns(pk_col)
        if not rows or not columns:
            return False
        values: list[tuple[str, ...]] = []
        for row in rows:
            if not self._has_complete_pk(row, pk_col):
                return False
            values.append(tuple(str(row.get(col)) for col in columns))
        return len(set(values)) == len(values)

    def _row_hash(self, row: dict) -> str:
        return json.dumps(
            {k: v for k, v in row.items() if k != "content"},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )

    @staticmethod
    def _normalize_fk_value(value: str) -> str:
        """FK 值归一化: 大写并去掉分隔符，容错 SUP-001 / sup_001 / SUP001 等格式差异"""
        import re
        return re.sub(r'[\s\-_]', '', str(value)).upper()

    def _row_identity_value(self, row: dict, pk_col: str | None) -> str:
        columns = self._pk_columns(pk_col)
        if columns and self._has_complete_pk(row, pk_col):
            if len(columns) == 1:
                # Preserve IDs already materialized for single-column keys.
                return f"{columns[0]}:{row.get(columns[0])}"
            payload = {
                "columns": columns,
                "values": [row.get(col) for col in columns],
            }
            return "composite_pk:" + json.dumps(
                payload, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), default=str)
        return f"row_hash:{self._row_hash(row)}"

    def _lookup_identity_value(self, pk_col: str | None, value: str) -> str:
        columns = self._pk_columns(pk_col)
        if len(columns) == 1:
            return f"{columns[0]}:{value}"
        if len(columns) > 1 and isinstance(value, (list, tuple)) and len(value) == len(columns):
            return "composite_pk:" + json.dumps(
                {"columns": columns, "values": list(value)},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                default=str)
        return value

    def _stable_row_id(self, mapping: OntologyMapping, row: dict, pk_col: str | None) -> str:
        identity = self._row_identity_value(row, pk_col)
        return str(_uuid.uuid5(_uuid.NAMESPACE_URL, f"{mapping.ontology_id}:{mapping.entity_class}:{identity}"))

    def _stable_relation_id(self, ontology_id: str, src_eid: str, tgt_eid: str, rel_type: str, source: str) -> str:
        return str(_uuid.uuid5(
            _uuid.NAMESPACE_URL,
            f"{ontology_id}:{src_eid}:{rel_type}:{tgt_eid}:{source}",
        ))

    def _infer_cardinality(self, source_ids: list[str], target_ids: list[str]) -> str:
        if not source_ids or not target_ids:
            return "unknown"
        source_unique = len(set(source_ids)) == len(source_ids)
        target_unique = len(set(target_ids)) == len(target_ids)
        if source_unique and target_unique:
            return "one_to_one"
        if source_unique and not target_unique:
            return "many_to_one"
        if not source_unique and target_unique:
            return "one_to_many"
        return "many_to_many"

    def _has_display_value(self, value) -> bool:
        if value in (None, ""):
            return False
        try:
            return value == value
        except Exception:
            return True

    def _join_display_parts(self, row: dict, cols: tuple[str, ...], min_parts: int = 1) -> str | None:
        parts = []
        for col in cols:
            value = row.get(col)
            if self._has_display_value(value):
                parts.append(str(value))
        if len(parts) >= min_parts:
            return " / ".join(parts)
        return None

    def _display_name(self, mapping: OntologyMapping, row: dict, pk_col: str | None, index: int) -> str:
        row_type = str(row.get("row_type") or "")
        source_file = row.get("source_file") or row.get("filename")
        if row_type == "rule":
            condition = str(row.get("condition") or "").strip()
            suffix = f" #{row.get('rule_index')}" if row.get("rule_index") not in (None, "") else ""
            return f"{source_file or mapping.entity_class} / Rule{suffix}: {condition[:80]}".strip()
        if row_type == "table_row":
            section = row.get("section_title") or "Table"
            table_idx = row.get("table_index") or 1
            row_idx = row.get("table_row_index") or index + 1
            return f"{source_file or mapping.entity_class} / {section} / T{table_idx}R{row_idx}"
        if row_type == "section":
            section = row.get("section_title") or "Section"
            section_idx = row.get("section_index") or index + 1
            return f"{source_file or mapping.entity_class} / {section} #{section_idx}"
        if row.get("record_id") not in (None, "") and pk_col == "__row_hash__":
            return str(row.get("record_id"))

        if len(self._pk_columns(pk_col)) > 1 and self._has_complete_pk(row, pk_col):
            return " / ".join(
                f"{col}={row.get(col)}" for col in self._pk_columns(pk_col))

        entity_class_lower = (mapping.entity_class or "").lower()
        if "order" in entity_class_lower or "订单" in str(mapping.entity_class or ""):
            order_label = self._join_display_parts(row, ("order_id", "order_name"), min_parts=1)
            if order_label:
                return order_label
            order_label = self._join_display_parts(row, ("订单号", "订单名称"), min_parts=1)
            if order_label:
                return order_label

        for cols in (
            ("order_id", "items.sku"),
            ("order_id", "items.name"),
            ("订单号", "物料编码"),
        ):
            label = self._join_display_parts(row, cols, min_parts=2)
            if label:
                return label

        inventory_label = self._join_display_parts(row, ("日期", "物料编码", "操作类型", "所在仓库"), min_parts=3)
        if inventory_label:
            return f"{inventory_label} #{index + 1}"

        supplier_label = self._join_display_parts(row, ("供应商ID", "供应商名称"), min_parts=2)
        if supplier_label:
            return supplier_label

        if self._has_complete_pk(row, pk_col):
            col = self._pk_columns(pk_col)[0]
            return str(row.get(col))

        candidates = []
        for col in row.keys():
            lower = col.lower()
            if any(token in lower for token in ("name", "title")) or any(token in col for token in ("名称", "标题", "文件名")):
                candidates.append(col)
        for col in (
            pk_col,
            "order_id", "订单号", "运单号", "供应商ID", "物料编码",
            "filename", "source_file",
        ):
            if col and col != "__row_hash__" and col in row:
                candidates.append(col)
        for col in candidates:
            value = row.get(col)
            if value not in (None, ""):
                return str(value)
        return f"{mapping.entity_class} #{index + 1}"

    def _first_value(self, row: dict, cols: list[str]) -> str | None:
        for col in cols:
            if not col or col == "__row_hash__" or col not in row:
                continue
            value = row.get(col)
            if self._has_display_value(value):
                return str(value)
        return None

    def _identity_columns(self, row: dict, pk_col: str | None) -> list[str]:
        cols = list(row.keys())
        result: list[str] = []
        result.extend(self._pk_columns(pk_col))
        for col in cols:
            lower = col.lower()
            if (
                lower == "id"
                or lower.endswith("_id")
                or col.endswith("ID")
                or "编号" in col
                or "编码" in col
                or col in ("订单号", "运单号", "单号")
            ):
                result.append(col)
        return list(dict.fromkeys(result))

    def _name_columns(self, row: dict) -> list[str]:
        result: list[str] = []
        for col in row.keys():
            lower = col.lower()
            if any(token in lower for token in ("name", "title")) or any(token in col for token in ("名称", "姓名", "标题")):
                result.append(col)
        return result

    def _has_cjk(self, value: str | None) -> bool:
        return bool(value and re.search(r"[\u4e00-\u9fff]", value))

    def _instance_names(self, mapping: OntologyMapping, row: dict, pk_col: str | None, index: int) -> dict[str, str]:
        """Return row-instance names. These must describe the record, not the schema/table."""
        display = self._display_name(mapping, row, pk_col, index)
        id_value = self._first_value(row, self._identity_columns(row, pk_col))
        name_value = self._first_value(row, self._name_columns(row))

        if not self._has_cjk(display):
            en = display
        elif id_value and name_value and id_value != name_value and not self._has_cjk(name_value):
            en = f"{id_value} / {name_value}"
        else:
            en = display

        return {
            "display_name": display,
            "name_cn": str(display)[:200],
            "name_en": str(en)[:200],
        }

    def _rows_to_entities(self, mapping: OntologyMapping, rows: list[dict]) -> list[dict]:
        field_map = mapping.field_mapping or {}
        pk_col = field_map.get("__primary_key__") or self._choose_pk_col(rows)
        property_meta = self._property_metadata_by_column(field_map)
        runtime_keys = {
            "id", "ontology_id", "source_id", "object_type", "source_row_count",
            "name", "name_cn", "name_en", "display_name", "__mapping_ids__",
        }
        entities_by_id: dict[str, dict] = {}
        for index, row in enumerate(rows):
            props: dict = {"ontology_id": mapping.ontology_id}
            business_runtime_values: dict = {}
            for col, prop in field_map.items():
                if col.startswith("__"):
                    continue
                if property_meta.get(col, {}).get("hidden"):
                    continue
                if col in row:
                    if prop in runtime_keys:
                        business_runtime_values[prop] = row[col]
                    else:
                        props[prop] = row[col]
            if len(self._pk_columns(pk_col)) > 1:
                # Formal ObjectType currently exposes one primary_key property.
                # Preserve every component as required business fields and add a
                # deterministic scalar identity solely for that compatibility
                # slot; instance IDs continue to derive from the same JSON value.
                props["__composite_identity__"] = self._row_identity_value(
                    row, pk_col)
            props["id"] = self._stable_row_id(
                mapping, row, pk_col if self._has_complete_pk(row, pk_col) else None)
            props["source_id"] = props["id"]
            props.update(self._instance_names(mapping, row, pk_col, index))
            props["name"] = props["name_cn"]
            props["object_type"] = mapping.entity_class
            # Ownership metadata powers source-of-truth reconciliation.  It is
            # reserved platform metadata and is not exposed as a business
            # ObjectType property by formal_projection.
            props["__mapping_ids__"] = [mapping.id]
            if business_runtime_values:
                # Entity's legacy envelope owns keys such as id/name.  Keep
                # explicitly mapped business properties separately so the
                # Formal projection can restore them without corrupting the
                # runtime identity/display metadata.
                props["__business_properties__"] = business_runtime_values
            if props["id"] in entities_by_id:
                existing = entities_by_id[props["id"]]
                existing["source_row_count"] = int(existing.get("source_row_count", 1)) + 1
                continue
            props["source_row_count"] = 1
            entities_by_id[props["id"]] = props
        return list(entities_by_id.values())

    def _write_v1_entities(self, mapping: OntologyMapping, entities: list[dict]) -> int:
        from app.models.entity import Entity
        count = 0
        try:
            for props in entities:
                eid = props["id"]
                name_cn = props.get("name_cn") or props.get("display_name") or eid
                name_en = props.get("name_en") or props.get("display_name") or eid
                other = {k: v for k, v in props.items() if k not in ("id", "ontology_id")}
                existing = self._db.query(Entity).filter(
                    Entity.id == eid, Entity.ontology_id == mapping.ontology_id).first()
                previous_owners = set(
                    (existing.properties or {}).get("__mapping_ids__", [])
                    if isinstance(existing, Entity) else [])
                other["__mapping_ids__"] = sorted(previous_owners | {mapping.id})
                self._db.merge(Entity(
                    id=eid, ontology_id=mapping.ontology_id,
                    name_cn=str(name_cn)[:200], name_en=str(name_en)[:200],
                    type=mapping.entity_class, properties=other,
                    confidence=mapping.confidence or 0.85,
                ))
                count += 1
            self._db.flush()
        except Exception as e:
            logger.exception("v1 entities 写入失败")
            self._db.rollback()
            raise MappingApplyError(f"v1 entities 写入失败: {e}") from e
        return count

    def _adopt_legacy_projection_ownership(self, mapping: OntologyMapping) -> None:
        """Backfill provenance for pre-hardening Entity rows before reconciliation.

        Adoption is safe only when one mapping owns the ontology/entity_class.  If
        several mappings could have produced the same legacy rows, deleting by
        guess would be worse than refusing the apply.
        """
        from app.models.entity import Entity

        unowned = [
            entity for entity in self._db.query(Entity).filter(
                Entity.ontology_id == mapping.ontology_id,
                Entity.type == mapping.entity_class,
            ).all()
            if not (entity.properties or {}).get("__mapping_ids__")
        ]
        if not unowned:
            return
        owners = self._db.query(OntologyMapping).filter(
            OntologyMapping.ontology_id == mapping.ontology_id,
            OntologyMapping.entity_class == mapping.entity_class,
        ).count()
        if owners != 1:
            raise MappingApplyError(
                f"实体类型 {mapping.entity_class} 存在 {len(unowned)} 条无来源的历史投影，"
                f"且有 {owners} 个映射可能拥有它们；拒绝猜测删除归属，请先做一次性血缘迁移")
        for entity in unowned:
            props = dict(entity.properties or {})
            props["__mapping_ids__"] = [mapping.id]
            entity.properties = props
        self._db.flush()

    def _reconcile_mapping_entities(
        self, mapping: OntologyMapping, current_entity_ids: set[str],
    ) -> list[str]:
        """Remove current-state projections absent from the new lake snapshot.

        Immutable PropertyFact history is retained; formal object/link tombstones
        are appended before deleting the materialized current-state rows.
        """
        from sqlalchemy import or_
        from app.models.entity import Entity
        from app.models.relation import Relation
        from app.models.ontology_formal import ObjectInstance, LinkInstance
        from app.ontologies.formal_modeling.facts import (
            record_link_fact, record_object_tombstone)

        removed: list[str] = []
        candidates = self._db.query(Entity).filter(
            Entity.ontology_id == mapping.ontology_id).all()
        for entity in candidates:
            props = dict(entity.properties or {})
            owners = set(props.get("__mapping_ids__") or [])
            if mapping.id not in owners or entity.id in current_entity_ids:
                continue
            owners.discard(mapping.id)
            if owners:
                props["__mapping_ids__"] = sorted(owners)
                entity.properties = props
                continue

            # Legacy current-state edges are derived from the object projection.
            for relation in self._db.query(Relation).filter(
                Relation.ontology_id == mapping.ontology_id,
                or_(Relation.source_entity == entity.id,
                    Relation.target_entity == entity.id),
            ).all():
                self._db.delete(relation)

            instance = self._db.query(ObjectInstance).filter(
                ObjectInstance.ontology_id == mapping.ontology_id,
                ObjectInstance.external_id == entity.id,
                ObjectInstance.source == "pipeline",
            ).first()
            if instance is not None:
                links = self._db.query(LinkInstance).filter(
                    LinkInstance.ontology_id == mapping.ontology_id,
                    or_(LinkInstance.source_object_id == instance.id,
                        LinkInstance.target_object_id == instance.id),
                ).all()
                for link in links:
                    record_link_fact(
                        self._db, ontology_id=mapping.ontology_id,
                        link_instance_id=link.id, link_type_id=link.link_type_id,
                        exists=False, source=f"mapping://{mapping.id}")
                    self._db.delete(link)
                record_object_tombstone(
                    self._db, ontology_id=mapping.ontology_id,
                    instance_id=instance.id, object_type_id=instance.object_type_id,
                    source=f"mapping://{mapping.id}")
                self._db.delete(instance)
            self._db.delete(entity)
            removed.append(entity.id)
        self._db.flush()
        return removed

    # ── Logic / Action Discovery ───────────────────────────────────

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
        automation_desc = "When a curated dataset is approved, incremental ontology mapping can upsert objects, links, vectors, logic and actions."
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

    def _write_neo4j(self, entity_class: str, entities: list[dict]) -> int:
        try:
            from app.services.v2.graph.neo4j_service import Neo4jService
            neo = Neo4jService()
            if neo.available:
                count = neo.batch_upsert_entities(entity_class, entities)
                neo.close()
                return count
        except Exception as e:
            logger.error(f"Neo4j 写入失败: {e}")
        return 0

    def _delete_neo4j_entities(self, ontology_id: str, entity_ids: list[str]) -> int:
        if not entity_ids:
            return 0
        try:
            from app.services.v2.graph.neo4j_service import Neo4jService
            neo = Neo4jService()
            if neo.available:
                count = neo.batch_delete_entities(ontology_id, entity_ids)
                neo.close()
                return count
        except Exception as e:
            # Relational/Formal current state remains authoritative and has
            # already committed.  Health/repair tooling can rebuild this derived
            # projection; never roll back truth because a cache is unavailable.
            logger.error("Neo4j stale-node reconciliation failed: %s", e)
        return 0

    def _rebuild_neo4j_projection(self, ontology_id: str) -> bool:
        """Rebuild the derived graph after relation reconciliation.

        Neo4j cannot join the SQL transaction.  Rebuild-after-commit gives it a
        deterministic repair path and removes stale relationships instead of
        accumulating them forever.
        """
        from app.models.entity import Entity
        from app.models.relation import Relation
        neo = None
        try:
            from app.services.v2.graph.neo4j_service import Neo4jService
            neo = Neo4jService()
            if not neo.available:
                return False
            neo.delete_by_ontology(ontology_id)
            entities = self._db.query(Entity).filter(
                Entity.ontology_id == ontology_id).all()
            by_type: dict[str, list[dict]] = {}
            for entity in entities:
                props = {"id": entity.id, "ontology_id": ontology_id,
                         **dict(entity.properties or {})}
                by_type.setdefault(entity.type or "Object", []).append(props)
            for label, rows in by_type.items():
                neo.batch_upsert_entities(label, rows)
            entity_type = {entity.id: entity.type or "Object" for entity in entities}
            for relation in self._db.query(Relation).filter(
                    Relation.ontology_id == ontology_id).all():
                src_type = entity_type.get(relation.source_entity)
                tgt_type = entity_type.get(relation.target_entity)
                if not src_type or not tgt_type:
                    continue
                neo.upsert_relation(
                    src_type, relation.source_entity, tgt_type,
                    relation.target_entity, relation.type,
                    props={"id": relation.id, "ontology_id": ontology_id,
                           "confidence": relation.confidence,
                           **dict(relation.properties or {})})
            return True
        except Exception as e:
            logger.warning("Neo4j projection rebuild failed: %s", e)
            return False
        finally:
            if neo is not None:
                try:
                    neo.close()
                except Exception:
                    pass

    def _rebuild_chroma_projection(self, ontology_id: str) -> int | None:
        """Replace the semantic-search projection from relational current state."""
        from app.models.entity import Entity
        try:
            from app.services.v2.vector.chroma_service import ChromaService
            chroma = ChromaService()
            if not chroma.available:
                return None
            name = f"ontology_{ontology_id}"
            # Delete may return false when the collection does not exist; upsert
            # below creates it.  Other failures are contained by the service and
            # surfaced as a zero count in the mapping result.
            chroma.delete_collection(name)
            entities = self._db.query(Entity).filter(
                Entity.ontology_id == ontology_id).all()
            payload = [{
                "id": entity.id,
                "type": entity.type or "Object",
                "name_cn": entity.name_cn,
                "name_en": entity.name_en,
                "confidence": entity.confidence,
                "properties": dict(entity.properties or {}),
            } for entity in entities]
            written = chroma.upsert_entities(ontology_id, payload) if payload else 0
            if payload and written != len(payload):
                return None
            if chroma.count(ontology_id) != len(payload):
                return None
            return written
        except Exception as e:
            logger.warning("Chroma projection rebuild failed: %s", e)
            return None

    def _write_neo4j_relations(self, ontology_id: str, src_class: str, tgt_class: str, rel_type: str) -> None:
        from app.models.relation import Relation
        from app.models.entity import Entity
        try:
            from app.services.v2.graph.neo4j_service import Neo4jService
            neo = Neo4jService()
            if not neo.available:
                return
            rels = self._db.query(Relation).filter(
                Relation.ontology_id == ontology_id, Relation.type == rel_type,
            ).all()
            for r in rels:
                neo.upsert_relation(src_class, r.source_entity,
                                    tgt_class, r.target_entity, rel_type,
                                    props={"id": r.id, "ontology_id": ontology_id,
                                           "confidence": r.confidence})
            neo.close()
        except Exception as e:
            logger.warning(f"Neo4j relation 写入失败（非致命）: {e}")

    # ── FK 检测（4 级策略）─────────────────────────────────────────

    def _detect_fk_columns(
        self, src_cols: list[str], tgt_entity_class: str, tgt_dataset_name: str,
        src_sample_rows: list[dict] | None = None,
        tgt_pk_values: set[str] | None = None,
    ) -> list[tuple[str, str]]:
        """多级 FK 检测: 1)标准_id/.id 2)语义词 3)值重叠 4)值模式 5)LLM"""
        candidates = []
        import re
        tgt_lower = tgt_entity_class.lower()
        tgt_name_lower = (tgt_dataset_name or "").lower()
        tgt_parts = [p.lower() for p in re.split(r'[_\-\s]|(?<=[a-z])(?=[A-Z])', tgt_entity_class) if p]
        tgt_parts.extend([p.lower() for p in re.split(r'[_\-\s]', tgt_name_lower) if p])

        for col in src_cols:
            col_lower = col.lower().rstrip("s")
            # 点号(JSON flatten 产物)与空格/连字符统一归一化为下划线
            col_clean = re.sub(r'[\s\-\.]', '_', col_lower)

            is_standard_fk = col_clean.endswith("_id") or col.endswith("Id") or col.endswith("ID")
            if is_standard_fk:
                col_prefix = re.sub(r'[_]?id$', '', col_clean)
                if (col_prefix in tgt_lower or tgt_lower in col_prefix or
                    any(part in col_prefix for part in tgt_parts if len(part) > 2)):
                    rel_name = col_prefix.upper().replace("-", "_") or tgt_lower.upper()
                    rel_type = f"HAS_{rel_name}" if not rel_name.startswith("HAS_") else rel_name
                    candidates.append((col, rel_type))
                    continue

            col_words = set(re.split(r'[_\-\s]', col_clean))
            tgt_keywords = set(tgt_parts) | {tgt_lower, tgt_name_lower}
            semantic_match = {w for w in (col_words & tgt_keywords) if len(w) > 1}
            if semantic_match:
                rel_name = max(semantic_match, key=len).upper().replace("-", "_")
                rel_type = f"HAS_{rel_name}" if not rel_name.startswith("HAS_") else rel_name
                candidates.append((col, rel_type))
                continue

            # 值重叠检测: 列值与目标主键值高度重合即判定 FK(对中文列名等无法靠列名匹配的情况有效)
            if tgt_pk_values and src_sample_rows:
                tgt_norm = {self._normalize_fk_value(v) for v in tgt_pk_values}
                sample_vals = [str(row.get(col, "")).strip() for row in src_sample_rows[:20] if row.get(col) not in (None, "")]
                if len(sample_vals) >= 2:
                    matched = sum(1 for v in sample_vals if self._normalize_fk_value(v) in tgt_norm)
                    if matched >= 2 and matched / len(sample_vals) >= 0.5:
                        rel_name = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', tgt_entity_class).upper()
                        rel_name = re.sub(r'[^A-Z0-9_]', '', rel_name) or "REF"
                        candidates.append((col, f"HAS_{rel_name}"))
                        continue

            if src_sample_rows and len(src_sample_rows) > 0:
                sample_vals = [str(row.get(col, "")) for row in src_sample_rows[:10] if row.get(col)]
                id_matches = [v for v in sample_vals if re.match(r'^[A-Za-z]+[-_]?\d+$', v)]
                if len(id_matches) >= 2:
                    prefixes = [re.match(r'^[A-Za-z]+', v) for v in id_matches]
                    prefixes = [m.group(0).upper() for m in prefixes if m]
                    if prefixes:
                        rel_type = f"HAS_{max(set(prefixes), key=prefixes.count)}"
                        candidates.append((col, rel_type))

        # 策略 4: LLM 辅助语义 FK 检测（默认关闭，避免构建时被外部服务阻塞）
        import os
        llm_fk_enabled = os.getenv("ENABLE_LLM_FK_DETECTION", "").lower() in ("1", "true", "yes")
        if llm_fk_enabled and not candidates and src_cols:
            try:
                llm_candidates = self._llm_detect_fk(src_cols, tgt_entity_class, tgt_dataset_name)
                candidates.extend(llm_candidates)
            except Exception:
                pass

        return candidates

    def _llm_detect_fk(self, src_cols: list[str], tgt_entity_class: str, tgt_dataset_name: str) -> list[tuple[str, str]]:
        """使用用户配置的 LLM 检测中文列名→英文实体名的 FK 关系"""
        try:
            from app.services import llm_service
            from app.services.model_config_selector import llm_call_kwargs, select_llm_model_config
            import json

            call_kwargs = llm_call_kwargs(select_llm_model_config(
                self._db,
                purpose_tags=("FK检测", "关系推断", "Link推断"),
                allow_vlm=False,
            ))
            if not call_kwargs:
                return []

            prompt = f"""判断以下列中哪些是外键指向目标实体。
源列名: {json.dumps(src_cols, ensure_ascii=False)}
目标实体: {tgt_entity_class}
目标数据集: {tgt_dataset_name}
规则：列名语义关联目标实体（如中文"供应商"→Supplier），或列值像ID。
返回JSON对象 {{"links":[{{"column":"列名","relation_type":"HAS_XXX"}}]}}，无匹配返回 {{"links":[]}}。只返回JSON。"""
            raw = llm_service._call_llm(
                **call_kwargs,
                messages=[{"role": "system", "content": "输出JSON。"}, {"role": "user", "content": prompt}]
            )
            result = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(result, dict):
                result = result.get("links", [])
            if isinstance(result, list):
                return [(r["column"], r["relation_type"]) for r in result if r.get("column")]
            return []
        except Exception:
            return []
    # ── Link Mapping 处理 ──

    def _process_link_mappings(self, ontology_id: str, mapping_meta: dict) -> list[dict]:
        from app.models.v2.mapping import OntologyLinkMapping, OntologyMapping as OM

        links = self._db.query(OntologyLinkMapping).filter(
            OntologyLinkMapping.ontology_id == ontology_id,
            OntologyLinkMapping.status == "active",
        ).all()
        results = []
        for link in links:
            src_meta = tgt_meta = None
            for mid, meta in mapping_meta.items():
                m = self._db.query(OM).filter(OM.id == mid).first()
                if not m: continue
                if m.curated_dataset_id == link.src_dataset_id: src_meta = meta
                if m.curated_dataset_id == link.tgt_dataset_id: tgt_meta = meta
            if not src_meta or not tgt_meta: continue
            # edge_dataset_id 有值 → 连接表「胖关系」（边可带属性）；否则 → 直连外键「瘦关系」
            if getattr(link, "edge_dataset_id", None):
                results.append(self._process_edge_table_link(ontology_id, link, src_meta, tgt_meta))
            else:
                results.append(self._process_direct_fk_link(ontology_id, link, src_meta, tgt_meta))
        return results

    def _pk_value_to_eid(self, meta: dict) -> dict[str, str]:
        """endpoint 对象映射的「主键值 → 实体 id」查找表：供连接表外键反查两端实体。"""
        out: dict[str, str] = {}
        pk_col = meta["pk_col"]
        for row in meta["rows"]:
            eid = meta["entity_id_map"].get(self._row_identity_value(row, pk_col))
            v = str(row.get(pk_col, "")).strip()
            if v and eid:
                out[v] = eid
        return out

    def _process_direct_fk_link(self, ontology_id: str, link, src_meta: dict, tgt_meta: dict) -> dict:
        """直连外键瘦关系：源表某列 == 目标表某列 → 建边（无自有属性）。原行为保持不变。"""
        from app.models.relation import Relation

        # 行→实体 id 必须按行自身的主键标识反查，不能 zip(rows, dict.items())——
        # entity_id_map 按 pk 去重后条目数可能少于行数，zip 会截断且错位连边
        def _eid_of(meta: dict, row: dict) -> str | None:
            return meta["entity_id_map"].get(self._row_identity_value(row, meta["pk_col"]))

        tgt_val_to_eid = {}
        for row in tgt_meta["rows"]:
            eid = _eid_of(tgt_meta, row)
            v = str(row.get(link.tgt_key, "")).strip()
            if v and eid: tgt_val_to_eid[v] = eid
        written = 0
        current_relation_ids: set[str] = set()
        src_values: list[str] = []
        tgt_values: list[str] = []
        for row in src_meta["rows"]:
            src_eid = _eid_of(src_meta, row)
            src_val = str(row.get(link.src_key, "")).strip()
            if not src_val or not src_eid: continue
            tgt_eid = tgt_val_to_eid.get(src_val)
            if not tgt_eid: continue
            src_values.append(src_eid)
            tgt_values.append(tgt_eid)
            relation_id = self._stable_relation_id(
                ontology_id, src_eid, tgt_eid, link.relation_type,
                f"link_mapping:{link.id}")
            rel = Relation(
                id=relation_id,
                ontology_id=ontology_id,
                source_entity=src_eid, target_entity=tgt_eid,
                type=link.relation_type,
                properties={"mapping_type": "link_mapping", "src_key": link.src_key,
                            "tgt_key": link.tgt_key, "__link_mapping_id__": link.id},
                confidence=0.9,
            )
            self._db.merge(rel); current_relation_ids.add(relation_id); written += 1
        cardinality = self._infer_cardinality(src_values, tgt_values)
        for stale_rel in self._db.query(Relation).filter(
                Relation.ontology_id == ontology_id).all():
            if ((stale_rel.properties or {}).get("__link_mapping_id__") == link.id
                    and stale_rel.id not in current_relation_ids):
                self._db.delete(stale_rel)
        if written:
            for rel in self._db.query(Relation).filter(
                Relation.ontology_id == ontology_id,
                Relation.type == link.relation_type,
            ).all():
                props = dict(rel.properties or {})
                if props.get("mapping_type") == "link_mapping" and props.get("src_key") == link.src_key and props.get("tgt_key") == link.tgt_key:
                    props["cardinality"] = cardinality
                    rel.properties = props
            self._db.flush()
        self._record_link_mapping_versions(link, src_meta, tgt_meta)
        self._db.flush()
        if written:
            logger.info("Link: " + src_meta["entity_class"] + "-[" + str(link.relation_type) + "]->" + tgt_meta["entity_class"] + " " + str(written) + "条")
        return {"src": src_meta["entity_class"], "tgt": tgt_meta["entity_class"],
                "rel_type": link.relation_type, "src_key": link.src_key, "tgt_key": link.tgt_key,
                "link_mapping_id": link.id,
                "count": written, "cardinality": cardinality,
                "warning": None if written else "No rows matched this link mapping"}

    def _process_edge_table_link(self, ontology_id: str, link, src_meta: dict, tgt_meta: dict) -> dict:
        """连接表胖关系：遍历连接表每行，两端外键各自反查实体，按 field_mapping 采集边属性。

        同一对实体之间可有多条边（各连接表行独立成边），稳定 id 纳入连接表行主键防去重合并。
        """
        from app.models.relation import Relation
        from app.data_channel.curated.review_service import (
            latest_dataset_version, load_all_rows_with_edits)
        from app.models.v2.dataset import Dataset
        from app.models.v2.curated import CuratedDataset
        from app.config import settings

        fmap = {k: v for k, v in dict(getattr(link, "field_mapping", None) or {}).items()
                if not str(k).startswith("__")}
        edge_version = latest_dataset_version(self._db, link.edge_dataset_id)
        try:
            edge_dataset = self._db.query(Dataset).filter(
                Dataset.id == link.edge_dataset_id).first()
            if edge_dataset:
                if edge_dataset.kind == "curated":
                    edge_rows = load_all_rows_with_edits(
                        self._db, link.edge_dataset_id,
                        require_approved=True, version=edge_version)
                else:
                    from app.services.v2.dataset_service import DatasetService
                    edge_rows = DatasetService(self._db).load_all_rows(
                        link.edge_dataset_id,
                        edge_version.version_no if edge_version is not None else None)
            else:
                legacy = self._db.query(CuratedDataset).filter(
                    CuratedDataset.id == link.edge_dataset_id).first()
                if settings.environment == "production" or legacy is None or legacy.status != "approved":
                    raise MappingSourceError("legacy 连接表不允许进入生产投影")
                from app.services.v2.dataset_service import DatasetService
                edge_rows = DatasetService(self._db).preview(
                    link.edge_dataset_id, None, limit=None)
        except Exception as e:  # noqa: BLE001
            raise MappingSourceError(
                f"关系映射 {link.id} 的连接表 {link.edge_dataset_id} 无法严格读取/审批: {e}") from e
        if not edge_rows:
            raise MappingSourceError(
                f"关系映射 {link.id} 的连接表当前审批版本为空，拒绝空跑")

        src_v2e = self._pk_value_to_eid(src_meta)
        tgt_v2e = self._pk_value_to_eid(tgt_meta)
        edge_pk_col = self._choose_pk_col(edge_rows)
        pending: list[tuple] = []
        src_values: list[str] = []
        tgt_values: list[str] = []
        for erow in edge_rows:
            sv = str(erow.get(link.src_key, "")).strip()
            tv = str(erow.get(link.tgt_key, "")).strip()
            if not sv or not tv:
                continue
            src_eid = src_v2e.get(sv)
            tgt_eid = tgt_v2e.get(tv)
            if not src_eid or not tgt_eid:
                continue
            edge_props = {prop: erow.get(col) for prop, col in fmap.items() if col in erow}
            edge_key = self._row_identity_value(erow, edge_pk_col)
            pending.append((src_eid, tgt_eid, edge_props, edge_key))
            src_values.append(src_eid)
            tgt_values.append(tgt_eid)

        cardinality = self._infer_cardinality(src_values, tgt_values)
        written = 0
        current_relation_ids: set[str] = set()
        for src_eid, tgt_eid, edge_props, edge_key in pending:
            props = {"mapping_type": "link_mapping", "__edge_key__": edge_key,
                     "__link_mapping_id__": link.id,
                     "cardinality": cardinality, **edge_props}
            relation_id = self._stable_relation_id(
                ontology_id, src_eid, tgt_eid, link.relation_type,
                f"link_mapping:{link.id}:{edge_key}")
            rel = Relation(
                id=relation_id,
                ontology_id=ontology_id,
                source_entity=src_eid, target_entity=tgt_eid,
                type=link.relation_type,
                properties=props,
                confidence=1.0,
            )
            self._db.merge(rel)
            current_relation_ids.add(relation_id)
            written += 1
        for stale_rel in self._db.query(Relation).filter(
                Relation.ontology_id == ontology_id).all():
            if ((stale_rel.properties or {}).get("__link_mapping_id__") == link.id
                    and stale_rel.id not in current_relation_ids):
                self._db.delete(stale_rel)
        if written:
            self._db.flush()
        self._record_link_mapping_versions(
            link, src_meta, tgt_meta,
            edge_version_id=getattr(edge_version, "id", None))
        self._db.flush()
        if written:
            logger.info("Link(连接表): %s-[%s]->%s %d条 边属性=%s",
                        src_meta["entity_class"], link.relation_type, tgt_meta["entity_class"],
                        written, list(fmap.keys()))
        return {"src": src_meta["entity_class"], "tgt": tgt_meta["entity_class"],
                "rel_type": link.relation_type, "src_key": link.src_key, "tgt_key": link.tgt_key,
                "link_mapping_id": link.id,
                "edge_dataset_id": link.edge_dataset_id, "edge_properties": list(fmap.keys()),
                "count": written, "cardinality": cardinality,
                "warning": None if written else "连接表未匹配到任何两端实体"}

    def _record_link_mapping_versions(
        self, link, src_meta: dict, tgt_meta: dict,
        *, edge_version_id: str | None = None,
    ) -> None:
        """Persist exact immutable lake versions consumed by a LinkMapping."""
        from app.data_channel.curated.review_service import latest_dataset_version
        from app.config import settings

        version_pairs = (
            ("source", link.src_dataset_id, src_meta.get("source_dataset_version_id")),
            ("target", link.tgt_dataset_id, tgt_meta.get("source_dataset_version_id")),
            ("edge", link.edge_dataset_id, edge_version_id),
        )
        field_mapping = dict(link.field_mapping or {})
        for role, dataset_id, version_id in version_pairs:
            if not dataset_id:
                continue
            current = latest_dataset_version(self._db, dataset_id)
            if current is None and settings.environment != "production":
                # Read-only compatibility for pre-canonical CuratedDataset rows.
                # Production release gates reject these because no immutable
                # DatasetVersion lineage can be proven.
                continue
            if current is None or current.id != version_id:
                current_label = f"v{current.version_no}" if current is not None else "无版本"
                raise MappingApplyError(
                    f"关系映射 {link.id} 执行期间 {role} 数据集已更新为 {current_label}；"
                    "拒绝记录过期投影")
            field_mapping[f"__applied_{role}_version_id__"] = version_id
        link.field_mapping = field_mapping
        self._db.flush()
