import json
import re


def _call_llm(provider: str, api_key: str, api_base: str | None, model: str, messages: list, json_mode: bool = True) -> str:
    # Stable seed for reproducibility: derived from message content so same input → same seed.
    import hashlib as _hashlib, json as _json
    try:
        _seed_src = _json.dumps([m.get("content", "")[:500] for m in messages], ensure_ascii=False, sort_keys=True)
        _seed = int(_hashlib.md5(_seed_src.encode()).hexdigest()[:8], 16)
    except Exception:
        _seed = 42

    if provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model, max_tokens=8192, temperature=0,
            system=messages[0]["content"],
            messages=[{"role": "user", "content": messages[1]["content"] + ("\n\n```json\n{" if json_mode else "")}],
        )
        return ("{" + resp.content[0].text) if json_mode else resp.content[0].text
    else:
        import openai
        kwargs = {"api_key": api_key}
        if api_base:
            kwargs["base_url"] = api_base
        client = openai.OpenAI(**kwargs)
        create_kwargs: dict = {"model": model, "messages": messages, "timeout": 300, "max_tokens": 65536,
                               "temperature": 0, "seed": _seed}
        if json_mode:
            create_kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = client.chat.completions.create(**create_kwargs)
        except Exception:
            # seed not supported by all providers — retry without it
            create_kwargs.pop("seed", None)
            resp = client.chat.completions.create(**create_kwargs)
        return resp.choices[0].message.content or ""



def _parse_response(raw: str) -> dict:
    if not raw:
        raise ValueError("Empty LLM response")

    # Strip markdown code fences (```json ... ``` or ``` ... ```)
    text = raw.strip()
    text = re.sub(r'^```(?:json)?\s*\n?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\n?```\s*$', '', text).strip()

    # Remove control characters that are illegal inside JSON strings
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)

    # Fast path: well-formed JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try json_repair (handles unescaped quotes, truncated output, etc.)
    try:
        from json_repair import repair_json
        repaired = repair_json(text)
        result = json.loads(repaired)
        if isinstance(result, dict):
            return result
    except Exception:
        pass

    # Last resort: slice from first { to last } and try again
    start, end = text.find('{'), text.rfind('}')
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Cannot parse LLM response as JSON: {raw[:300]}")
