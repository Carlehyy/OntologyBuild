from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta, timezone
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


def _iso_utc(dt) -> str | None:
    """序列化时间戳并标注 UTC 时区。

    created_at 存的是 UTC，但 SQLite 读回为 naive datetime，直接 isoformat()
    会丢失时区，前端 new Date() 会误当本地时间解析（如东八区偏移 8 小时）。
    这里为 naive 值补上 UTC 时区，保证输出带 +00:00 偏移。
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


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
        from app.ontologies.agent_runtime.llm_bridge import chat as llm_chat, LLMError

        call_kwargs = llm_call_kwargs(c)
        if not call_kwargs:
            raise ValueError("Model config must include at least one model name")
        resp = llm_chat(call_kwargs, [{"role": "user", "content": "ping"}], [])
        return {"data": {"ok": True, "response": resp.get("content", "")}}
    except LLMError as e:
        raise HTTPException(400, f"Connection failed: {e}")
    except Exception as e:
        raise HTTPException(400, f"Connection failed: {e}")


@router.get("/{model_id}/stats")
def get_model_stats(model_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    """返回模型调用统计 — 用于模型卡片展示。"""
    from app.model_configs.models import ModelCallLog

    c = db.query(ModelConfig).filter(ModelConfig.id == model_id).first()
    if not c:
        raise HTTPException(404, "Not found")

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    thirty_days_ago = now - timedelta(days=30)

    # 今日调用数
    today_calls = db.query(func.count(ModelCallLog.id)).filter(
        ModelCallLog.model_config_id == model_id,
        ModelCallLog.created_at >= today_start,
    ).scalar() or 0

    # 30天总调用
    total_30d = db.query(func.count(ModelCallLog.id)).filter(
        ModelCallLog.model_config_id == model_id,
        ModelCallLog.created_at >= thirty_days_ago,
    ).scalar() or 0

    # 30天成功数
    success_30d = db.query(func.count(ModelCallLog.id)).filter(
        ModelCallLog.model_config_id == model_id,
        ModelCallLog.created_at >= thirty_days_ago,
        ModelCallLog.status == "success",
    ).scalar() or 0

    # 可用率
    availability = round(success_30d / total_30d * 100, 1) if total_30d > 0 else None

    # 平均延迟（30天）
    avg_latency = db.query(func.avg(ModelCallLog.latency_ms)).filter(
        ModelCallLog.model_config_id == model_id,
        ModelCallLog.created_at >= thirty_days_ago,
    ).scalar()
    avg_latency = round(avg_latency, 1) if avg_latency else None

    # 最近调用
    last_call = db.query(ModelCallLog).filter(
        ModelCallLog.model_config_id == model_id,
    ).order_by(ModelCallLog.created_at.desc()).first()

    # 近60次调用（热力条）— 始终返回 60 格，不足的用灰色填充
    recent_60 = db.query(ModelCallLog).filter(
        ModelCallLog.model_config_id == model_id,
    ).order_by(ModelCallLog.created_at.desc()).limit(60).all()
    recent_60.reverse()  # 正序：早 → 近

    heat_cells = []
    for log in recent_60:
        if log.status == "success":
            if log.latency_ms < 500:
                color = "#216e39"
            elif log.latency_ms < 1000:
                color = "#2d8a4e"
            elif log.latency_ms < 3000:
                color = "#40c463"
            else:
                color = "#9be9a8"
            cell_status = "success"
            title = f"成功 {log.latency_ms}ms"
        elif log.status == "error":
            color = "#e5484d"
            cell_status = "error"
            title = f"异常: {log.error_message or '未知错误'}"
        else:
            color = "#f0a020"
            cell_status = "timeout"
            title = f"超时 {log.latency_ms}ms"
        heat_cells.append({
            "color": color,
            "title": title,
            "status": cell_status,
        })

    # 不足60格时，左侧（更早的位置）补灰色空位
    empty_slots = 60 - len(heat_cells)
    for _ in range(empty_slots):
        heat_cells.insert(0, {
            "color": "#eceef1",
            "title": "暂无调用记录",
            "status": "none",
        })

    return {
        "data": {
            "todayCalls": today_calls,
            "availability": f"{availability}" if availability is not None else None,
            "avgLatency": avg_latency,
            "lastCall": _iso_utc(last_call.created_at) if last_call else None,
            "successRate": round(success_30d / total_30d * 100, 1) if total_30d > 0 else None,
            "heatCells": heat_cells,
        }
    }
