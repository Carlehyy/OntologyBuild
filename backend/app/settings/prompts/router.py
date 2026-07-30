from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
from app.deps import get_db, get_current_user, require_admin
from app.settings.prompts.models import Prompt
from app.models.extraction_task import ExtractionTask
from app.models.user import User
from app.model_configs.models import ModelConfig
from app.settings.prompts.schemas import PromptCreate, PromptUpdate, PromptOut
from app.settings.prompts.templates import BUILTIN_PROMPTS
import uuid

router = APIRouter()

@router.get("/templates")
def get_builtin_templates(_=Depends(get_current_user)):
    """Return hardcoded builtin prompt templates (not from DB)."""
    return {"data": BUILTIN_PROMPTS}

@router.get("")
def list_prompts(domain: Optional[str] = None, db: Session = Depends(get_db), _=Depends(get_current_user)):
    q = db.query(Prompt)
    if domain:
        q = q.filter(Prompt.domain == domain)
    prompts = q.order_by(Prompt.created_at.desc()).all()
    return {"data": [PromptOut.model_validate(p).model_dump() for p in prompts]}

@router.post("", status_code=201)
def create_prompt(body: PromptCreate, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    prompt = Prompt(id=str(uuid.uuid4()), name=body.name, domain=body.domain,
                    content=body.content, version=body.version, created_by=current_user.id)
    db.add(prompt); db.commit(); db.refresh(prompt)
    return {"data": PromptOut.model_validate(prompt).model_dump()}

@router.get("/by-domain/{domain}")
def get_prompts_by_domain(domain: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    prompts = db.query(Prompt).filter(Prompt.domain == domain).all()
    return {"data": [PromptOut.model_validate(p).model_dump() for p in prompts]}

@router.get("/{prompt_id}")
def get_prompt(prompt_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    p = db.query(Prompt).filter(Prompt.id == prompt_id).first()
    if not p:
        raise HTTPException(404, "Not found")
    return {"data": PromptOut.model_validate(p).model_dump()}

@router.put("/{prompt_id}")
def update_prompt(prompt_id: str, body: PromptUpdate, db: Session = Depends(get_db), _=Depends(require_admin)):
    p = db.query(Prompt).filter(Prompt.id == prompt_id).first()
    if not p:
        raise HTTPException(404, "Not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(p, k, v)
    db.commit(); db.refresh(p)
    return {"data": PromptOut.model_validate(p).model_dump()}

@router.delete("/{prompt_id}", status_code=204)
def delete_prompt(prompt_id: str, db: Session = Depends(get_db), _=Depends(require_admin)):
    p = db.query(Prompt).filter(Prompt.id == prompt_id).first()
    if not p:
        raise HTTPException(404, "Not found")
    db.query(ExtractionTask).filter(ExtractionTask.prompt_id == prompt_id).update(
        {ExtractionTask.prompt_id: None}, synchronize_session=False
    )
    db.delete(p)
    db.commit()

@router.post("/generate-template")
def generate_prompt_template(
    domain: str = Query(..., description="业务域"),
    style: str = Query("ontology_extraction", description="提示词风格"),
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """Use LLM to generate a prompt template for a given business domain"""
    from app.services.llm_service import _call_llm
    from app.services.encryption_service import decrypt

    model_cfg = db.query(ModelConfig).first()
    if not model_cfg:
        raise HTTPException(400, "No model configured. Please add a model in the Models page first.")

    provider = model_cfg.provider
    api_key = decrypt(model_cfg.api_key_encrypted or "")
    api_base = model_cfg.api_base
    models_list = model_cfg.models or []
    model_name = models_list[0] if models_list else ""
    if not model_name:
        raise HTTPException(400, "Model name not configured.")

    system_msg = (
        "你是一个本体工程专家，擅长为不同业务域设计 LLM 提取提示词。"
        "根据用户指定的业务域，生成一个完整的本体提取 Prompt。"
        "Prompt 需要：1) 列出该域典型实体类型；2) 列出关系类型；3) 要求提取逻辑规则和动作；"
        "4) 规定返回 JSON 格式（entities/relations/logic_rules/actions）。"
        "只返回 Prompt 文本本身，不要有其他说明。"
    )
    user_msg = f"请为【{domain}】业务域生成本体提取提示词，风格：{style}。"

    try:
        content = _call_llm(provider, api_key, api_base, model_name, [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ], json_mode=False)
        if not isinstance(content, str):
            content = str(content)
    except Exception as e:
        raise HTTPException(500, f"LLM generation failed: {str(e)}")

    return {"domain": domain, "content": content.strip()}
