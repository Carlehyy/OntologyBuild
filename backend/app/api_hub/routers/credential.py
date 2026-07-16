from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import credential, db, scheduler

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


class CronBody(BaseModel):
    cron: str


class CredentialConfigBody(BaseModel):
    username: str = ""
    password: str | None = None
    login_url: str = ""
    clear_password: bool = False


@router.get("/config")
def get_config():
    return credential.public_configuration()


@router.put("/config")
def put_config(body: CredentialConfigBody):
    if not body.username.strip():
        raise HTTPException(status_code=400, detail="W3 账号不能为空")
    try:
        return credential.update_configuration(
            username=body.username,
            password=body.password,
            login_url=body.login_url,
            clear_password=body.clear_password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/usage")
def get_usage(limit: int = 60):
    return db.credential_usage_stats(limit)


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
