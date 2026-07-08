from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.deps import get_db, get_current_user
from app.models.model_config import ModelConfig
from app.models.extraction_task import ExtractionTask
from app.models.user import User
from app.schemas.model_config import ModelConfigCreate, ModelConfigUpdate, ModelConfigOut
from app.services.encryption_service import encrypt
import uuid

router = APIRouter()


def _set_default(db: Session, config: ModelConfig) -> None:
    db.query(ModelConfig).filter(ModelConfig.id != config.id).update(
        {ModelConfig.is_default: False}, synchronize_session=False
    )
    config.is_default = True


def _ensure_default(db: Session) -> None:
    if db.query(ModelConfig).filter(ModelConfig.is_default.is_(True)).first():
        return
    fallback = db.query(ModelConfig).filter(ModelConfig.enabled.is_(True)).order_by(ModelConfig.updated_at.desc()).first()
    if fallback:
        fallback.is_default = True


def _model_out(config: ModelConfig) -> dict:
    data = ModelConfigOut.model_validate(config).model_dump()
    data["has_api_key"] = bool(config.api_key_encrypted)
    return data


@router.get("")
def list_models(db: Session = Depends(get_db), _=Depends(get_current_user)):
    configs = db.query(ModelConfig).order_by(ModelConfig.updated_at.desc()).all()
    return {"data": [_model_out(c) for c in configs]}


@router.post("", status_code=201)
def create_model(body: ModelConfigCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    config = ModelConfig(
        id=str(uuid.uuid4()),
        name=body.name,
        config_type=body.config_type or "llm",
        provider=body.provider,
        api_base=body.api_base,
        api_key_encrypted=encrypt(body.api_key or ""),
        models=body.models,
        options=body.options or {},
        enabled=body.enabled,
        is_default=body.is_default,
        created_by=current_user.id,
    )
    db.add(config)
    db.flush()
    if config.is_default:
        _set_default(db, config)
    elif not db.query(ModelConfig).filter(ModelConfig.is_default.is_(True)).first():
        config.is_default = True
    db.commit(); db.refresh(config)
    return {"data": _model_out(config)}


@router.get("/{model_id}")
def get_model(model_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    c = db.query(ModelConfig).filter(ModelConfig.id == model_id).first()
    if not c:
        raise HTTPException(404, "Not found")
    return {"data": _model_out(c)}


@router.put("/{model_id}")
def update_model(model_id: str, body: ModelConfigUpdate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    c = db.query(ModelConfig).filter(ModelConfig.id == model_id).first()
    if not c:
        raise HTTPException(404, "Not found")
    if body.name is not None:
        c.name = body.name
    if body.config_type is not None:
        c.config_type = body.config_type
    if body.provider is not None:
        c.provider = body.provider
    if body.api_key is not None:
        c.api_key_encrypted = encrypt(body.api_key)
    if body.api_base is not None:
        c.api_base = body.api_base
    if body.models is not None:
        c.models = body.models
    if body.options is not None:
        c.options = body.options
    if body.enabled is not None:
        c.enabled = body.enabled
    if body.is_default is True:
        c.enabled = True
        _set_default(db, c)
    elif body.is_default is False and c.is_default:
        c.is_default = False
        _ensure_default(db)
    db.commit(); db.refresh(c)
    return {"data": _model_out(c)}


@router.delete("/{model_id}", status_code=204)
def delete_model(model_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    c = db.query(ModelConfig).filter(ModelConfig.id == model_id).first()
    if not c:
        raise HTTPException(404, "Not found")
    db.query(ExtractionTask).filter(ExtractionTask.model_id == model_id).update(
        {ExtractionTask.model_id: None}, synchronize_session=False
    )
    was_default = c.is_default
    db.delete(c)
    if was_default:
        _ensure_default(db)
    db.commit()


@router.post("/{model_id}/default")
def set_default_model(model_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    c = db.query(ModelConfig).filter(ModelConfig.id == model_id).first()
    if not c:
        raise HTTPException(404, "Not found")
    c.enabled = True
    _set_default(db, c)
    db.commit(); db.refresh(c)
    return {"data": _model_out(c)}


@router.post("/{model_id}/enabled")
def set_model_enabled(model_id: str, body: dict, db: Session = Depends(get_db), _=Depends(get_current_user)):
    c = db.query(ModelConfig).filter(ModelConfig.id == model_id).first()
    if not c:
        raise HTTPException(404, "Not found")
    if "enabled" not in body:
        raise HTTPException(400, "enabled is required")
    c.enabled = bool(body["enabled"])
    if not c.enabled and c.is_default:
        c.is_default = False
        _ensure_default(db)
    db.commit(); db.refresh(c)
    return {"data": _model_out(c)}


@router.post("/{model_id}/test")
def test_model(model_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    c = db.query(ModelConfig).filter(ModelConfig.id == model_id).first()
    if not c:
        raise HTTPException(404, "Not found")
    try:
        if not c.enabled:
            return {"data": {"ok": False, "response": "Model config is disabled."}}

        if (c.config_type or "llm") == "ocr":
            if c.provider == "easyocr":
                import os
                enabled = os.getenv("ENABLE_OCR", "").lower() in ("1", "true", "yes") or bool((c.options or {}).get("enabled"))
                if not enabled:
                    return {"data": {"ok": False, "response": "EasyOCR is configured but disabled. Enable it in OCR model config or set ENABLE_OCR=1."}}
                import easyocr  # noqa: F401
                return {"data": {"ok": True, "response": "EasyOCR import ok"}}
            if c.provider == "paddleocr":
                import os
                enabled = (
                    os.getenv("ENABLE_OCR", "").lower() in ("1", "true", "yes")
                    or os.getenv("ENABLE_PADDLEOCR", "").lower() in ("1", "true", "yes")
                    or bool((c.options or {}).get("enabled"))
                )
                if not enabled:
                    return {"data": {"ok": False, "response": "PaddleOCR is configured but disabled. Enable it in OCR model config or set ENABLE_OCR=1."}}
                from paddleocr import PaddleOCR  # noqa: F401
                return {"data": {"ok": True, "response": "PaddleOCR import ok"}}
            if c.provider == "external_api":
                if not c.api_base:
                    raise HTTPException(400, "External OCR requires API Base")
                return {"data": {"ok": True, "response": "External OCR endpoint configured"}}
            return {"data": {"ok": True, "response": f"OCR provider configured: {c.provider}"}}

        if (c.config_type or "llm") != "llm":
            return {"data": {"ok": True, "response": f"Config type configured: {c.config_type}"}}

        from app.services.model_config_selector import llm_call_kwargs
        call_kwargs = llm_call_kwargs(c)
        if not call_kwargs:
            raise ValueError("Model config must include at least one model name")
        api_key = call_kwargs["api_key"]
        if c.provider == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            model = call_kwargs["model"]
            resp = client.messages.create(model=model, max_tokens=10, messages=[{"role": "user", "content": "ping"}])
            return {"data": {"ok": True, "response": resp.content[0].text}}
        else:
            import openai
            kwargs = {"api_key": api_key}
            if call_kwargs["api_base"]:
                kwargs["base_url"] = call_kwargs["api_base"]
            client = openai.OpenAI(**kwargs)
            model = call_kwargs["model"]
            resp = client.chat.completions.create(model=model, messages=[{"role": "user", "content": "ping"}], max_tokens=10)
            return {"data": {"ok": True, "response": resp.choices[0].message.content}}
    except Exception as e:
        raise HTTPException(400, f"Connection failed: {e}")
