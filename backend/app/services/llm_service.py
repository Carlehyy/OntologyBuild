"""
LLM Service - Pluggable LLM provider for extraction and inference.
Supports: OpenAI, Ollama (local), and "none" (deterministic fallback).
System MUST work even without any API key.
"""

import json
import re
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

# Dynamic import
try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    print("[WARN] OpenAI package not available - LLM features disabled")


@dataclass
class ExtractionCandidate:
    candidate_type: str
    object_type: Optional[str] = None
    name: Optional[str] = None
    properties: Optional[Dict[str, Any]] = None
    relation_type: Optional[str] = None
    source_name: Optional[str] = None
    target_name: Optional[str] = None
    confidence: float = 0.0
    reasoning: Optional[str] = None


class LLMService:
    """Pluggable LLM service with deterministic fallback."""

    def __init__(self):
        self.provider = "none"
        self.api_key = ""
        self.model = ""
        self.ollama_url = ""
        self.ollama_model = ""
        self._client = None
        self._max_tokens = 4000
        self._temperature = 0.1

        try:
            from app.config import get_settings
            settings = get_settings()
            self.provider = settings.llm_provider.lower()
            self.api_key = settings.openai_api_key
            self.model = settings.openai_model
            self.ollama_url = settings.ollama_base_url
            self.ollama_model = settings.ollama_model
            self._max_tokens = settings.extraction_max_tokens
            self._temperature = settings.extraction_temperature

            if self.provider == "openai" and self.api_key and OPENAI_AVAILABLE:
                self._client = openai.AsyncOpenAI(api_key=self.api_key)
        except Exception as e:
            print(f"[LLM] Init warning: {e}")

    @property
    def is_available(self) -> bool:
        if self.provider == "openai":
            return self._client is not None and bool(self.api_key) and OPENAI_AVAILABLE
        elif self.provider == "ollama":
            return True
        return False

    async def extract_from_text(self, text: str,
                                object_types: List[Dict[str, Any]],
                                relation_types: List[Dict[str, Any]]) -> List[ExtractionCandidate]:
        """Extract entities/relations. Falls back to deterministic extraction."""
        if not self.is_available or not text.strip():
            return self._fallback_extraction(text, object_types, relation_types)

        try:
            prompt = self._build_extraction_prompt(text, object_types, relation_types)

            if self.provider == "openai" and self._client:
                response = await self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": "You are a precise extraction system. Output only valid JSON."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                    response_format={"type": "json_object"},
                )
                return self._parse_extraction_result(response.choices[0].message.content)

            elif self.provider == "ollama":
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{self.ollama_url}/api/generate",
                        json={"model": self.ollama_model, "prompt": prompt, "stream": False, "format": "json"},
                        timeout=aiohttp.ClientTimeout(total=120),
                    ) as resp:
                        result = await resp.json()
                        return self._parse_extraction_result(result.get("response", "{}"))

        except Exception as e:
            print(f"[LLM] Extraction failed: {e}. Falling back.")
            return self._fallback_extraction(text, object_types, relation_types)

        return []

    async def answer_question(self, query: str, context: str,
                              object_types: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Answer question with LLM. Falls back to context-only."""
        if not self.is_available or not context.strip():
            return {
                "answer": f"Available context:\n\n{context[:2000]}" if context else "No context available.",
                "confidence": 0.5,
                "reasoning": "LLM unavailable. Context-only response." if not self.is_available else "Empty context.",
            }

        try:
            prompt = self._build_qa_prompt(query, context, object_types)

            if self.provider == "openai" and self._client:
                response = await self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": "You are a helpful knowledge graph assistant."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.3,
                    max_tokens=2000,
                )
                return {"answer": response.choices[0].message.content, "confidence": 0.85,
                        "reasoning": "Generated by LLM with graph context."}

            elif self.provider == "ollama":
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{self.ollama_url}/api/generate",
                        json={"model": self.ollama_model, "prompt": prompt, "stream": False},
                        timeout=aiohttp.ClientTimeout(total=120),
                    ) as resp:
                        result = await resp.json()
                        return {"answer": result.get("response", ""), "confidence": 0.75,
                                "reasoning": "Generated by Ollama."}

        except Exception as e:
            print(f"[LLM] Q&A failed: {e}")
            return {"answer": f"Context:\n\n{context[:2000]}", "confidence": 0.5,
                    "reasoning": f"LLM error: {str(e)[:100]}. Context fallback."}

    def _build_extraction_prompt(self, text: str, object_types: List[Dict],
                                 relation_types: List[Dict]) -> str:
        ot_desc = "\n".join([
            f"- {ot['name']}: {ot.get('description', '')}\n  Properties: {', '.join([p['name'] for p in ot.get('properties', [])])}"
            for ot in object_types
        ]) if object_types else "No object types defined. Extract general entities."

        rt_desc = "\n".join([
            f"- {rt['name']}: {rt.get('description', '')}"
            for rt in relation_types
        ]) if relation_types else "No relation types defined."

        return f"""Extract structured entities and relationships from the text.

## Object Types:
{ot_desc}

## Relation Types:
{rt_desc}

## Output Format (JSON):
{{"entities": [{{"object_type": "TypeName", "name": "...", "properties": {{}}, "confidence": 0.95}}],
 "relations": [{{"relation_type": "TypeName", "source_name": "...", "target_name": "...", "confidence": 0.85}}]}}

## Text:
{text}

JSON Output:"""

    def _build_qa_prompt(self, query: str, context: str, object_types: List[Dict]) -> str:
        ot_desc = "\n".join([f"- {ot['name']}: {ot.get('description', '')}" for ot in object_types])
        return f"""Answer based on the graph context.

## Domain Ontology:
{ot_desc}

## Graph Context:
{context}

## Question: {query}

Answer concisely based ONLY on the context.## Answer:"""

    def _parse_extraction_result(self, content: str) -> List[ExtractionCandidate]:
        try:
            content = content.strip()
            if content.startswith("```json"): content = content[7:]
            if content.startswith("```"): content = content[3:]
            if content.endswith("```"): content = content[:-3]
            content = content.strip()

            data = json.loads(content)
            candidates = []
            for entity in data.get("entities", []):
                candidates.append(ExtractionCandidate(
                    candidate_type="entity", object_type=entity.get("object_type"),
                    name=entity.get("name"), properties=entity.get("properties", {}),
                    confidence=entity.get("confidence", 0.5), reasoning=entity.get("reasoning")))
            for relation in data.get("relations", []):
                candidates.append(ExtractionCandidate(
                    candidate_type="relation", relation_type=relation.get("relation_type"),
                    source_name=relation.get("source_name"), target_name=relation.get("target_name"),
                    confidence=relation.get("confidence", 0.5), reasoning=relation.get("reasoning")))
            return candidates
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"[LLM] Parse error: {e}")
            return []

    def _fallback_extraction(self, text: str, object_types: List[Dict],
                             relation_types: List[Dict]) -> List[ExtractionCandidate]:
        """Deterministic extraction - NEVER returns fake data."""
        candidates = []
        if not text or not text.strip():
            return candidates

        text_lower = text.lower()

        # If no ontology defined, use basic pattern detection
        if not object_types:
            pattern = r'\b([A-Z][a-zA-Z]*(?:\s+[A-Z][a-zA-Z]*)+)\b'
            matches = re.findall(pattern, text)
            seen = set()
            stop_words = {'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can', 'had',
                         'her', 'was', 'one', 'our', 'out', 'day', 'get', 'has', 'him', 'his',
                         'how', 'its', 'may', 'new', 'now', 'old', 'see', 'two', 'who', 'boy',
                         'did', 'she', 'use', 'way', 'many', 'oil', 'sit', 'set', 'run', 'eat',
                         'far', 'sea', 'eye', 'ago', 'off', 'too', 'any', 'say', 'man', 'try',
                         'ask', 'end', 'why', 'let', 'put', 'tell', 'very', 'when', 'much',
                         'would', 'there', 'their', 'what', 'said', 'have', 'each', 'which',
                         'will', 'about', 'could', 'other', 'after', 'first', 'never', 'these',
                         'think', 'where', 'being', 'every', 'great', 'might', 'shall', 'still',
                         'those', 'while', 'this', 'that', 'they', 'them', 'than', 'then', 'look',
                         'only', 'come', 'over', 'know', 'take', 'year', 'good', 'some', 'time',
                         'want', 'here', 'also', 'back', 'well', 'even', 'most', 'just', 'like',
                         'into', 'such', 'make', 'work', 'life', 'find', 'give', 'does', 'made',
                         'part', 'keep', 'call', 'came', 'need', 'feel', 'seem', 'turn', 'hand',
                         'high', 'sure', 'upon', 'head', 'help', 'home', 'side', 'move', 'both',
                         'five', 'once', 'same', 'must', 'name', 'left', 'done', 'open', 'case',
                         'show', 'live', 'play', 'went', 'told', 'seen', 'long', 'last', 'next'}
            for match in matches:
                name = match.strip()
                if len(name) > 2 and name not in seen and name.lower() not in stop_words:
                    seen.add(name)
                    candidates.append(ExtractionCandidate(
                        candidate_type="entity", object_type="Unknown", name=name,
                        properties={"detected_by": "fallback_pattern"}, confidence=0.3,
                        reasoning="Detected by capitalized word pattern (LLM unavailable)."))
            return candidates

        # Ontology-driven keyword matching
        for ot in object_types:
            ot_name = ot.get("name", "")
            keywords = [ot_name.lower()] + (ot.get("description", "") or "").lower().split()[:5]
            for keyword in keywords:
                if keyword in text_lower and len(keyword) > 2:
                    sentences = re.split(r'[.!?\n]+', text)
                    for sentence in sentences:
                        sent_lower = sentence.lower()
                        if keyword in sent_lower:
                            pattern = r'\b([A-Z][a-zA-Z]*(?:\s+[A-Z][a-zA-Z]*)+)\b'
                            matches = re.findall(pattern, sentence)
                            for match in matches:
                                name = match.strip()
                                if len(name) > 2:
                                    props = {}
                                    for prop in ot.get("properties", []):
                                        prop_name = prop.get("name", "")
                                        prop_pattern = rf'{re.escape(prop_name)}[:\s]+([^,;.\n]+)'
                                        prop_match = re.search(prop_pattern, sent_lower)
                                        if prop_match:
                                            props[prop_name] = prop_match.group(1).strip()
                                    candidates.append(ExtractionCandidate(
                                        candidate_type="entity", object_type=ot_name, name=name,
                                        properties=props, confidence=0.4,
                                        reasoning=f"Keyword '{keyword}' matched for '{ot_name}' (LLM fallback)."))
                    break
        return candidates


# Singleton
_llm_service: Optional[LLMService] = None


def get_llm_service() -> LLMService:
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
