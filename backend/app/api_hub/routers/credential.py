from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import credential, scheduler

router = APIRouter(prefix="/credential", tags=["api-hub-credential"])


@router.get("/status")
def get_status():
    st = credential.status()
    st["cron"] = scheduler.get_cron()
    st["next_run"] = scheduler.next_run()
    return st


@router.post("/refresh")
def refresh():
    st = credential.refresh()
    st["cron"] = scheduler.get_cron()
    st["next_run"] = scheduler.next_run()
    return st


@router.get("/cookie-header")
def cookie_header():
    """返回当前登录态拼成的 Cookie 头，供 cURL 导出复用（仅本机可访问）。"""
    saved = credential.load_saved()
    if not saved or not saved.get("cookies"):
        return {"cookie": "", "count": 0}
    parts = [f'{c["name"]}={c["value"]}' for c in saved["cookies"]]
    return {"cookie": "; ".join(parts), "count": len(parts)}


class CronBody(BaseModel):
    cron: str


@router.get("/schedule")
def get_schedule():
    return {"cron": scheduler.get_cron(), "next_run": scheduler.next_run()}


@router.put("/schedule")
def put_schedule(body: CronBody):
    try:
        scheduler.set_cron(body.cron.strip())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Cron 表达式无效，需 5 段（分 时 日 月 周），例如 0 */2 * * *",
        )
    return {"cron": scheduler.get_cron(), "next_run": scheduler.next_run()}
