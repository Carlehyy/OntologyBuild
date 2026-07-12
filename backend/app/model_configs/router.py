from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta, timezone
import re
from app.deps import get_db, get_current_user
from app.models.model_config import ModelConfig
from app.models.extraction_task import ExtractionTask
from app.models.user import User
from app.schemas.model_config import (
    ModelConfigCreate,
    ModelConfigImportRequest,
    ModelConfigOut,
    ModelConfigUpdate,
    ModelEnabledRequest,
)
from app.services.encryption_service import encrypt
import uuid

router = APIRouter()


def _set_default(db: Session, config: ModelConfig) -> None:
    if (config.config_type or "llm") != "llm":
        raise HTTPException(409, "只有 LLM 配置可以设为默认模型")
    db.query(ModelConfig).filter(
        ModelConfig.config_type == "llm",
        ModelConfig.id != config.id,
    ).update(
        {ModelConfig.is_default: False}, synchronize_session=False
    )
    config.is_default = True


def _ensure_default(db: Session) -> None:
    if db.query(ModelConfig).filter(
        ModelConfig.config_type == "llm",
        ModelConfig.enabled.is_(True),
        ModelConfig.is_default.is_(True),
    ).first():
        return
    fallback = db.query(ModelConfig).filter(
        ModelConfig.config_type == "llm",
        ModelConfig.enabled.is_(True),
    ).order_by(
        case((ModelConfig.last_test_status == "success", 0), else_=1),
        ModelConfig.updated_at.desc(),
    ).first()
    if fallback:
        fallback.is_default = True


def _name_exists(db: Session, name: str, exclude_id: str | None = None) -> bool:
    query = db.query(ModelConfig.id).filter(func.lower(ModelConfig.name) == name.strip().lower())
    if exclude_id:
        query = query.filter(ModelConfig.id != exclude_id)
    return query.first() is not None


def _require_tested(config: ModelConfig, action: str) -> None:
    if config.last_test_status != "success":
        raise HTTPException(409, f"请先完成连通性测试，再{action}")


def _safe_test_error(exc: Exception) -> tuple[str, str]:
    """把 SDK 错误收敛为可操作信息，并避免把令牌或长响应带到前端。"""
    raw = str(exc)
    lower = raw.lower()
    if any(token in lower for token in ("401", "unauthorized", "authentication", "invalid api key")):
        return "AUTH_FAILED", "认证失败，请检查 API Key 是否正确且仍然有效"
    if any(token in lower for token in ("404", "model_not_found", "model not found", "does not exist")):
        return "MODEL_NOT_FOUND", "模型不存在或当前账号无权访问，请检查模型名"
    if any(token in lower for token in ("429", "rate limit", "too many requests")):
        return "RATE_LIMITED", "请求被限流，请稍后重试或检查账号额度"
    if any(token in lower for token in ("timeout", "timed out")):
        return "TIMEOUT", "连接超时，请检查接入地址和网络后重试"
    if any(token in lower for token in ("connection", "connect", "dns", "name or service not known")):
        return "NETWORK_ERROR", "无法连接到服务，请检查 API Base 和网络"
    scrubbed = re.sub(r"(?i)(sk-[A-Za-z0-9_-]{8,}|bearer\s+[A-Za-z0-9._-]+)", "[已隐藏]", raw)
    scrubbed = " ".join(scrubbed.split())[:240]
    return "CONNECTION_FAILED", f"连接失败：{scrubbed or '服务未返回可识别的错误信息'}"


def _save_test_result(db: Session, config: ModelConfig, ok: bool, message: str) -> str:
    tested_at = datetime.now(timezone.utc)
    config.last_test_status = "success" if ok else "error"
    config.last_tested_at = tested_at
    config.last_test_message = message[:500]
    db.commit()
    return tested_at.isoformat()


def _commit_config_change(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "模型配置状态发生并发变更，请刷新后重试") from exc


def _model_out(config: ModelConfig) -> dict:
    data = ModelConfigOut.model_validate(config).model_dump()
    data["has_api_key"] = bool(config.api_key_encrypted)
    data["created_at"] = _iso_utc(config.created_at)
    data["updated_at"] = _iso_utc(config.updated_at)
    data["last_tested_at"] = _iso_utc(config.last_tested_at)
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
    if _name_exists(db, body.name):
        raise HTTPException(409, "模型配置名称已存在，请使用不同名称")
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
    if config.enabled:
        _require_tested(config, "启用")
    _commit_config_change(db); db.refresh(config)
    return {"data": _model_out(config)}


@router.post("/import", status_code=201)
def import_models(
    body: ModelConfigImportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """原子导入不含密钥的配置。为安全起见，所有导入项均保持停用和待测试。"""
    names = [item.name.strip().lower() for item in body.configs]
    if len(names) != len(set(names)):
        raise HTTPException(409, "导入文件中存在重复的配置名称")
    conflicts = {
        name for (name,) in db.query(func.lower(ModelConfig.name)).filter(
            func.lower(ModelConfig.name).in_(names)
        ).all()
    }
    if conflicts:
        raise HTTPException(409, f"以下配置名称已存在：{', '.join(sorted(conflicts))}")

    created: list[ModelConfig] = []
    try:
        for item in body.configs:
            config = ModelConfig(
                id=str(uuid.uuid4()),
                name=item.name,
                config_type=item.config_type or "llm",
                provider=item.provider,
                api_base=item.api_base,
                api_key_encrypted=encrypt(""),
                models=item.models,
                options=item.options or {},
                enabled=False,
                is_default=False,
                created_by=current_user.id,
            )
            db.add(config)
            created.append(config)
        db.flush()
        _commit_config_change(db)
    except Exception:
        db.rollback()
        raise
    for config in created:
        db.refresh(config)
    return {
        "data": {
            "imported": len(created),
            "configs": [_model_out(config) for config in created],
            "warning": "API Key 不会随配置文件导入，所有导入项已保持停用，请补充密钥并测试后启用。",
        }
    }


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
    original_connection = (
        c.config_type,
        c.provider,
        c.api_base or "",
        tuple(c.models or []),
        c.api_key_encrypted or "",
    )
    if body.name is not None and body.name.strip().lower() != c.name.strip().lower() and _name_exists(db, body.name, c.id):
        raise HTTPException(409, "模型配置名称已存在，请使用不同名称")
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
    current_connection = (
        c.config_type,
        c.provider,
        c.api_base or "",
        tuple(c.models or []),
        c.api_key_encrypted or "",
    )
    connection_changed = current_connection != original_connection
    if (c.config_type or "llm") == "llm" and not (c.models or []):
        raise HTTPException(422, "LLM 配置必须填写模型名")
    if connection_changed:
        c.last_test_status = None
        c.last_tested_at = None
        c.last_test_message = "连接参数已变更，请重新测试"
        if c.enabled:
            c.enabled = False
        if c.is_default:
            c.is_default = False
            _ensure_default(db)
    if body.enabled is not None:
        if body.enabled:
            _require_tested(c, "启用")
        c.enabled = body.enabled
    if body.is_default is True:
        if not c.enabled:
            raise HTTPException(409, "请先启用模型，再设为默认")
        _require_tested(c, "设为默认")
        _set_default(db, c)
    elif body.is_default is False and c.is_default:
        c.is_default = False
        _ensure_default(db)
    if c.is_default and ((c.config_type or "llm") != "llm" or not c.enabled):
        c.is_default = False
        _ensure_default(db)
    _commit_config_change(db); db.refresh(c)
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
    _commit_config_change(db)


@router.post("/{model_id}/default")
def set_default_model(model_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    c = db.query(ModelConfig).filter(ModelConfig.id == model_id).first()
    if not c:
        raise HTTPException(404, "Not found")
    if not c.enabled:
        raise HTTPException(409, "请先启用模型，再设为默认")
    _require_tested(c, "设为默认")
    _set_default(db, c)
    _commit_config_change(db); db.refresh(c)
    return {"data": _model_out(c)}


@router.post("/{model_id}/enabled")
def set_model_enabled(model_id: str, body: ModelEnabledRequest, db: Session = Depends(get_db), _=Depends(get_current_user)):
    c = db.query(ModelConfig).filter(ModelConfig.id == model_id).first()
    if not c:
        raise HTTPException(404, "Not found")
    if body.enabled:
        _require_tested(c, "启用")
    c.enabled = body.enabled
    if not c.enabled and c.is_default:
        c.is_default = False
        _ensure_default(db)
    _commit_config_change(db); db.refresh(c)
    return {"data": _model_out(c)}


@router.post("/{model_id}/test")
def test_model(model_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    # Import the exception class before entering provider-specific branches.
    # Otherwise an OCR import failure occurs before the later local import and
    # Python raises UnboundLocalError while trying to match ``except LLMError``.
    from app.ontologies.agent_runtime.llm_bridge import LLMError

    c = db.query(ModelConfig).filter(ModelConfig.id == model_id).first()
    if not c:
        raise HTTPException(404, "Not found")
    try:
        if (c.config_type or "llm") == "ocr":
            if c.provider == "easyocr":
                import os
                enabled = os.getenv("ENABLE_OCR", "").lower() in ("1", "true", "yes") or bool((c.options or {}).get("enabled"))
                if not enabled:
                    message = "EasyOCR 运行开关未开启"
                    tested_at = _save_test_result(db, c, False, message)
                    return {"data": {"ok": False, "response": message, "code": "OCR_DISABLED", "tested_at": tested_at}}
                import easyocr  # noqa: F401
                message = "连接成功，EasyOCR 运行环境正常"
                tested_at = _save_test_result(db, c, True, message)
                return {"data": {"ok": True, "response": message, "code": "OK", "tested_at": tested_at}}
            if c.provider == "paddleocr":
                import os
                enabled = (
                    os.getenv("ENABLE_OCR", "").lower() in ("1", "true", "yes")
                    or os.getenv("ENABLE_PADDLEOCR", "").lower() in ("1", "true", "yes")
                    or bool((c.options or {}).get("enabled"))
                )
                if not enabled:
                    message = "PaddleOCR 运行开关未开启"
                    tested_at = _save_test_result(db, c, False, message)
                    return {"data": {"ok": False, "response": message, "code": "OCR_DISABLED", "tested_at": tested_at}}
                from paddleocr import PaddleOCR  # noqa: F401
                message = "连接成功，PaddleOCR 运行环境正常"
                tested_at = _save_test_result(db, c, True, message)
                return {"data": {"ok": True, "response": message, "code": "OK", "tested_at": tested_at}}
            if c.provider == "external_api":
                if not c.api_base:
                    raise ValueError("External OCR requires API Base")
                message = "配置检查通过，外部 OCR 接入地址有效"
                tested_at = _save_test_result(db, c, True, message)
                return {"data": {"ok": True, "response": message, "code": "OK", "tested_at": tested_at}}
            message = f"配置检查通过，OCR Provider：{c.provider}"
            tested_at = _save_test_result(db, c, True, message)
            return {"data": {"ok": True, "response": message, "code": "OK", "tested_at": tested_at}}

        if (c.config_type or "llm") != "llm":
            message = f"配置检查通过：{c.config_type}"
            tested_at = _save_test_result(db, c, True, message)
            return {"data": {"ok": True, "response": message, "code": "OK", "tested_at": tested_at}}

        from app.services.model_config_selector import llm_call_kwargs
        from app.ontologies.agent_runtime.llm_bridge import chat as llm_chat

        call_kwargs = llm_call_kwargs(c)
        if not call_kwargs:
            raise ValueError("Model config must include at least one model name")
        # 连通性测试不计入业务调用统计，并严格限制输出，避免一次测试消耗大量额度。
        call_kwargs.pop("model_config_id", None)
        call_kwargs["max_output_tokens"] = min(int(call_kwargs.get("max_output_tokens") or 16), 16)
        call_kwargs["timeout_seconds"] = 30
        llm_chat(
            call_kwargs,
            [{"role": "user", "content": "Connectivity check. Reply with exactly PONG."}],
            [],
        )
        message = "连接成功，模型响应正常"
        tested_at = _save_test_result(db, c, True, message)
        return {"data": {"ok": True, "response": message, "code": "OK", "tested_at": tested_at}}
    except LLMError as e:
        code, message = _safe_test_error(e)
        tested_at = _save_test_result(db, c, False, message)
        return {"data": {"ok": False, "response": message, "code": code, "tested_at": tested_at}}
    except Exception as e:
        code, message = _safe_test_error(e)
        tested_at = _save_test_result(db, c, False, message)
        return {"data": {"ok": False, "response": message, "code": code, "tested_at": tested_at}}


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
        ModelCallLog.status == "success",
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
