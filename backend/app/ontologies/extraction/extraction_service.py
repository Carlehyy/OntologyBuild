"""
LLM 多轮抽取增强管道

流程: 长文档 → 切块 → 多轮抽取(实体→关系→规则) → 跨块去重 → 置信度校准 → 候选池

使用平台启动后由管理员启用的文本 LLM。未配置 LLM 时使用明确标识的
确定性规则模式；已配置 LLM 调用失败时显式失败，不悄悄切换算法。
"""
from __future__ import annotations
import logging
import json
import re
import uuid
from typing import Any
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class LLMExtractionError(RuntimeError):
    """A configured LLM failed; callers must surface the failure."""


class LLMExtractionService:
    """LLM multi-round extraction or an explicit non-LLM rules mode."""

    def __init__(self, call_kwargs: dict | None = None):
        self._call_kwargs = dict(call_kwargs or {})
        self.available = bool(self._call_kwargs)
        self.model_name = (
            str(self._call_kwargs.get("model") or "") or None
        )

    def extract_pipeline(self, text: str, ontology_id: str, domain: str = "金融风控") -> dict:
        """
        完整抽取管道:
        1. 文档切块
        2. 第一轮: 抽取实体
        3. 第二轮: 抽取关系
        4. 第三轮: 抽取规则
        5. 去重合并
        6. 置信度校准
        """
        if not self.available or not text.strip():
            return self._deterministic_rules(text, ontology_id)

        try:
            chunks = self._chunk_text(text)
            all_entities = []
            all_relations = []
            all_rules = []

            for i, chunk in enumerate(chunks):
                logger.info(f"Processing chunk {i+1}/{len(chunks)} ({len(chunk)} chars)")

                # Round 1: 实体抽取
                entities = self._extract_entities(chunk, domain)
                all_entities.extend(entities)

                # Round 2: 关系抽取
                relations = self._extract_relations(chunk, entities)
                all_relations.extend(relations)

                # Round 3: 规则抽取
                rules = self._extract_rules(chunk, domain)
                all_rules.extend(rules)

            # Deduplicate & calibrate
            deduped_entities = self._deduplicate_entities(all_entities)
            deduped_relations = self._deduplicate_relations(all_relations)
            calibrated_entities = self._calibrate_confidence(deduped_entities)
            calibrated_relations = self._calibrate_confidence(deduped_relations)

            return {
                "status": "completed",
                "method": "llm_multi_round",
                "chunks_processed": len(chunks),
                "entities": calibrated_entities,
                "relations": calibrated_relations,
                "rules": all_rules,
                "total_raw": len(all_entities) + len(all_relations),
                "total_deduped": len(calibrated_entities) + len(calibrated_relations),
            }

        except LLMExtractionError:
            raise
        except Exception as exc:
            raise LLMExtractionError(
                "已配置的 LLM 抽取失败，未切换到规则模式"
            ) from exc

    def _chunk_text(self, text: str, max_chars: int = 2000) -> list[str]:
        """简单文本切块 — 按句子边界分割"""
        if len(text) <= max_chars:
            return [text]

        chunks = []
        sentences = re.split(r'(?<=[。！？.!?])\s+', text)
        current = ""

        for s in sentences:
            if len(current) + len(s) > max_chars and current:
                chunks.append(current.strip())
                current = s
            else:
                current += s
        if current.strip():
            chunks.append(current.strip())

        return chunks if chunks else [text[:max_chars]]

    def _call_llm(self, system_prompt: str, user_content: str, temperature: float = 0.3) -> str:
        """Call the selected platform model through the shared gateway."""
        del temperature  # the shared gateway owns the platform call policy
        from app.model_configs.llm_gateway import chat

        result = chat(
            self._call_kwargs,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            [],
        )
        content = result.get("content")
        if not isinstance(content, str) or not content.strip():
            raise LLMExtractionError("LLM 未返回可解析文本")
        return content

    def _extract_entities(self, text: str, domain: str) -> list[dict]:
        """Round 1: 实体抽取"""
        system = f"""你是一位专业的{domain}领域知识图谱抽取专家。请从以下文本中抽取实体。
要求：
1. 识别所有企业名称、个人姓名、机构名称
2. 对每个实体给出：名称(name_cn)、英文名称(name_en,可选)、类型(type: 企业/个人/机构)、描述(description)
3. 同时抽取该实体的属性（如注册资本、成立日期、行业、风险等级等）
4. 返回严格JSON数组格式，不要任何额外文字

示例格式：
[{{"name_cn": "华融控股", "name_en": "Huarong Holdings", "type": "企业", "description": "金融控股集团", "properties": {{"industry": "金融", "risk_level": "中风险"}}}}]"""

        try:
            content = self._call_llm(system, text[:3000])
            # Extract JSON array
            match = re.search(r'\[.*\]', content, re.DOTALL)
            if match:
                entities = json.loads(match.group())
                for e in entities:
                    e["_source"] = "llm_extraction"
                    e["_round"] = 1
                    e["_confidence"] = 0.85
                    e["id"] = f"cand_{uuid.uuid4().hex[:8]}"
                return entities
        except Exception as exc:
            raise LLMExtractionError("LLM 实体抽取失败") from exc
        raise LLMExtractionError("LLM 实体抽取未返回 JSON 数组")

    def _extract_relations(self, text: str, context_entities: list[dict]) -> list[dict]:
        """Round 2: 关系抽取"""
        entity_names = [e.get("name_cn", "") for e in context_entities if e.get("name_cn")]
        if not entity_names:
            return []

        system = f"""从文本中抽取实体之间的关系。已知实体: {', '.join(entity_names[:20])}
要求：
1. 识别每对实体之间的关系类型（如：控股、参股、担保、任职、关联交易、互保、合作等）
2. 对每条关系给出：源实体(source)、目标实体(target)、关系类型(type)、属性(properties)
3. 返回严格JSON数组格式

示例：[{{"source": "华融控股", "target": "华融证券", "type": "控股", "properties": {{"ratio": 52.3}}}}]"""

        try:
            content = self._call_llm(system, text[:3000])
            match = re.search(r'\[.*\]', content, re.DOTALL)
            if match:
                relations = json.loads(match.group())
                for r in relations:
                    r["_source"] = "llm_extraction"
                    r["_round"] = 2
                    r["_confidence"] = 0.80
                    r["id"] = f"cand_rel_{uuid.uuid4().hex[:8]}"
                return relations
        except Exception as exc:
            raise LLMExtractionError("LLM 关系抽取失败") from exc
        raise LLMExtractionError("LLM 关系抽取未返回 JSON 数组")

    def _extract_rules(self, text: str, domain: str) -> list[dict]:
        """Round 3: 规则抽取"""
        system = f"""从以下{domain}领域文本中抽取风控规则或业务逻辑。
要求：
1. 识别文本中隐含的风险检测规则
2. 每条规则给出：名称(name_cn)、描述(description)、触发条件(formula)、风险等级(risk_level)
3. 返回严格JSON数组格式

示例：[{{"name_cn": "循环担保检测", "description": "检测循环担保链", "formula": "担保(A,B) AND 担保(B,C) AND 担保(C,A)", "risk_level": "高"}}]"""

        try:
            content = self._call_llm(system, text[:3000])
            match = re.search(r'\[.*\]', content, re.DOTALL)
            if match:
                rules = json.loads(match.group())
                for r in rules:
                    r["_source"] = "llm_extraction"
                    r["_round"] = 3
                    r["_confidence"] = 0.75
                return rules
        except Exception as exc:
            raise LLMExtractionError("LLM 规则抽取失败") from exc
        raise LLMExtractionError("LLM 规则抽取未返回 JSON 数组")

    @staticmethod
    def _deduplicate_entities(entities: list[dict]) -> list[dict]:
        """按名称去重，保留置信度最高的"""
        seen = {}
        for e in entities:
            key = e.get("name_cn", "").strip()
            if not key:
                continue
            if key not in seen or e.get("_confidence", 0) > seen[key].get("_confidence", 0):
                seen[key] = e
        return list(seen.values())

    @staticmethod
    def _deduplicate_relations(relations: list[dict]) -> list[dict]:
        """按(源,目标,类型)去重"""
        seen = {}
        for r in relations:
            key = (r.get("source", ""), r.get("target", ""), r.get("type", ""))
            if not all(key):
                continue
            if key not in seen:
                seen[key] = r
        return list(seen.values())

    @staticmethod
    def _calibrate_confidence(items: list[dict]) -> list[dict]:
        """置信度校准：有属性的+0.05，有描述的+0.03，封顶0.95"""
        for item in items:
            base = item.get("_confidence", 0.8)
            if item.get("properties"):
                base += 0.05
            if item.get("description"):
                base += 0.03
            item["_confidence"] = min(round(base, 2), 0.95)
        return items

    @staticmethod
    def _deterministic_rules(text: str, ontology_id: str) -> dict:
        """
        明确的非 LLM 模式：基于规则的文本分析，绝不返回假数据。
        """
        # 企业名称模式匹配
        company_patterns = [
            r'([\u4e00-\u9fff]{2,10}(?:集团|公司|银行|证券|信托|基金|保险|投资|控股|实业|科技|能源|商贸|建设|担保|股份|有限)[\u4e00-\u9fff]{0,10})',
            r'([\u4e00-\u9fff]{2,8}有限公司)',
            r'([\u4e00-\u9fff]{2,8}股份有限公司)',
        ]

        found_companies = set()
        for pat in company_patterns:
            for m in re.finditer(pat, text):
                name = m.group(1).strip()
                if len(name) >= 4:
                    found_companies.add(name)

        # 个人姓名模式
        person_patterns = [
            r'([\u4e00-\u9fff]{2,4})(?:先生|女士|博士|教授|经理|总裁|董事长|总经理|CEO|实控人|法定代表人)',
            r'(?:由|系|为)([\u4e00-\u9fff]{2,4})[担任|负责|控制|持股]',
        ]
        found_persons = set()
        for pat in person_patterns:
            for m in re.finditer(pat, text):
                name = m.group(1).strip()
                if len(name) >= 2:
                    found_persons.add(name)

        entities = []
        for name in found_companies:
            entities.append({
                "id": f"cand_{uuid.uuid4().hex[:8]}",
                "name_cn": name, "type": "企业",
                "description": f"从文本中提取: {name[:20]}",
                "properties": {},
                "_source": "deterministic", "_confidence": 0.65, "_round": 1,
            })
        for name in found_persons:
            entities.append({
                "id": f"cand_{uuid.uuid4().hex[:8]}",
                "name_cn": name, "type": "个人",
                "description": f"从文本中提取: {name}",
                "properties": {},
                "_source": "deterministic", "_confidence": 0.55, "_round": 1,
            })

        # 关系关键词匹配
        relation_keywords = {
            "控股": "控股", "参股": "参股", "持有": "控股",
            "担保": "担保", "保证": "担保", "互保": "互保",
            "担任": "任职", "任董事长": "任职", "任总经理": "任职",
            "关联交易": "关联交易", "合作": "合作",
        }
        relations = []
        entity_names = [e["name_cn"] for e in entities]
        for kw, rel_type in relation_keywords.items():
            if kw in text:
                # Find nearby entities
                idx = text.index(kw)
                nearby = []
                for name in entity_names:
                    ni = text.index(name) if name in text else -1
                    if ni >= 0 and abs(ni - idx) < 100:
                        nearby.append(name)
                if len(nearby) >= 2:
                    relations.append({
                        "id": f"cand_rel_{uuid.uuid4().hex[:8]}",
                        "source": nearby[0], "target": nearby[1],
                        "type": rel_type, "properties": {},
                        "_source": "deterministic", "_confidence": 0.50, "_round": 2,
                    })

        return {
            "status": "completed",
            "method": "deterministic_rules",
            "chunks_processed": 1,
            "entities": entities,
            "relations": relations[:20],
            "rules": [],
            "total_raw": len(entities) + len(relations),
            "total_deduped": len(entities) + len(relations),
            "note": "No enabled text LLM is configured; deterministic rules mode was selected explicitly",
        }

    def nl_to_cypher(self, question: str, ontology_id: str) -> dict:
        """NL→Cypher 翻译 (RAG问答增强)"""
        if not self.available:
            return {"cypher": "", "confidence": 0, "explanation": "LLM not available"}

        try:
            system = """你是知识图谱查询专家。将用户的中文自然语言问题转换为 Cypher 查询语句。
注意：
1. 只使用 MATCH、WHERE、RETURN、COUNT、LIMIT
2. 节点标签使用 OntologyEntity
3. 属性名使用 name_cn、type、description 等
4. 不要返回任何解释，只返回 JSON 格式：{"cypher": "...", "explanation": "..."}"""

            content = self._call_llm(system, f"本体ID: {ontology_id}\n问题: {question}", temperature=0.1)
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                result = json.loads(match.group())
                cypher = result.get("cypher")
                if not isinstance(cypher, str) or not cypher.strip():
                    raise LLMExtractionError(
                        "已配置的 LLM 查询翻译未返回 Cypher"
                    )
                return {
                    "cypher": cypher,
                    "explanation": result.get("explanation", ""),
                    "confidence": 0.85,
                }
        except LLMExtractionError:
            raise
        except Exception as exc:
            raise LLMExtractionError(
                "已配置的 LLM 查询翻译失败"
            ) from exc

        raise LLMExtractionError(
            "已配置的 LLM 查询翻译未返回 JSON 对象"
        )


def get_llm_extraction_service(db=None) -> LLMExtractionService:
    """Resolve the currently enabled platform text model for this request."""
    from app.model_configs.selector import (
        llm_call_kwargs,
        select_llm_model_config,
    )

    model_config = select_llm_model_config(
        db,
        purpose_tags=("本体抽取", "extraction"),
        allow_vlm=False,
    )
    return LLMExtractionService(llm_call_kwargs(model_config))
