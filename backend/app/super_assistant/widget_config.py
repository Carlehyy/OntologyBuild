"""悬浮 AI 助手（迷你超级助手）的页面可见范围配置。

平台级单例：管理员在 系统设置 → 超级助手 维护"隐藏名单"
（左导航叶子菜单键），所有登录用户的前端读取同一份配置决定右下角
悬浮入口是否渲染。

不挂在主 router 上：主 router 由 main.py 以 menu_guard("super_assistant")
整体保护，而本配置的读取方是全体登录用户（与菜单权限无关），因此
这里独立暴露 router，由 main.py 单独 include（GET 仅需登录，PUT 仅 admin）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.models import User
from app.deps import get_current_user, get_db, require_admin
from app.super_assistant.models import SuperAssistantWidgetConfig
from app.super_assistant.schemas import WidgetConfigOut, WidgetConfigUpdate

router = APIRouter()

_CONFIG_ID = "default"


def get_widget_config(db: Session) -> SuperAssistantWidgetConfig | None:
    return db.get(SuperAssistantWidgetConfig, _CONFIG_ID)


def save_widget_config(
    db: Session,
    hidden_menu_keys: list[str],
    updated_by: str,
) -> SuperAssistantWidgetConfig:
    row = db.get(SuperAssistantWidgetConfig, _CONFIG_ID)
    if row is None:
        row = SuperAssistantWidgetConfig(
            id=_CONFIG_ID,
            hidden_menu_keys=hidden_menu_keys,
            updated_by=updated_by,
        )
        db.add(row)
    else:
        row.hidden_menu_keys = hidden_menu_keys
        row.updated_by = updated_by
    db.commit()
    db.refresh(row)
    return row


@router.get("/widget-config", response_model=WidgetConfigOut)
def get_assistant_widget_config(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> WidgetConfigOut:
    row = get_widget_config(db)
    if row is None:
        # 从未配置过：全部页面可见（与功能上线前行为一致）
        return WidgetConfigOut(hidden_menu_keys=[], updated_at=None)
    return WidgetConfigOut.model_validate(row)


@router.put("/widget-config", response_model=WidgetConfigOut)
def update_assistant_widget_config(
    body: WidgetConfigUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> WidgetConfigOut:
    row = save_widget_config(db, body.hidden_menu_keys, current_user.id)
    return WidgetConfigOut.model_validate(row)
