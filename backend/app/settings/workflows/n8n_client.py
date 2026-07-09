from dataclasses import dataclass
from typing import Any, Optional

import httpx


class N8nApiError(Exception):
    """Raised when the n8n API returns a non-success response."""

    def __init__(self, status_code: int, message: str, body: Any = None):
        self.status_code = status_code
        self.message = message
        self.body = body
        super().__init__(f"HTTP {status_code}: {message}")


@dataclass
class N8nConnectionResult:
    ok: bool
    message: str
    api_base: str


class N8nClient:
    """Small HTTP client for the n8n public REST API."""

    def __init__(self, api_url: str, api_key: str, timeout_seconds: int = 10):
        self.api_base = normalize_n8n_api_base(api_url)
        self.api_key = api_key.strip()
        self.timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, str]:
        return {
            "X-N8N-API-KEY": self.api_key,
            "Accept": "application/json",
        }

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Any = None,
    ) -> Any:
        if not self.api_base:
            raise ValueError("n8n API URL is required")
        if not self.api_key:
            raise ValueError("n8n API key is required")

        url = f"{self.api_base}{path}"
        with httpx.Client(timeout=float(self.timeout_seconds)) as client:
            resp = client.request(
                method,
                url,
                params=params,
                json=json,
                headers=self._headers(),
            )

        if resp.status_code >= 400:
            body: Any
            try:
                body = resp.json()
            except ValueError:
                body = resp.text
            message = body.get("message", resp.text) if isinstance(body, dict) else resp.text
            raise N8nApiError(resp.status_code, message, body)

        if resp.status_code == 204 or not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    def get(self, path: str, params: Optional[dict[str, Any]] = None) -> Any:
        return self.request("GET", path, params=params)

    # ── Workflow 管理（数据管家使用的公共 REST API 子集） ──────────────

    #: POST/PUT /workflows 只接受这些顶层字段；带上 active/id/tags 等只读字段
    #: n8n 会直接 400（"request/body must NOT have additional properties"）
    WORKFLOW_WRITABLE_FIELDS = ("name", "nodes", "connections", "settings", "staticData")

    @staticmethod
    def sanitize_workflow(payload: dict) -> dict:
        body = {k: payload[k] for k in N8nClient.WORKFLOW_WRITABLE_FIELDS if k in payload}
        body.setdefault("settings", {})
        return body

    def list_workflows(self, *, active: Optional[bool] = None, limit: int = 50) -> list[dict]:
        params: dict[str, Any] = {"limit": max(1, min(limit, 250))}
        if active is not None:
            params["active"] = "true" if active else "false"
        data = self.get("/workflows", params=params)
        return data.get("data", []) if isinstance(data, dict) else []

    def get_workflow(self, workflow_id: str) -> dict:
        return self.get(f"/workflows/{workflow_id}")

    def create_workflow(self, payload: dict) -> dict:
        return self.request("POST", "/workflows", json=self.sanitize_workflow(payload))

    def update_workflow(self, workflow_id: str, payload: dict) -> dict:
        # PUT 要求完整文档：以远端现状为底座合并，避免缺字段被清空
        current = self.get_workflow(workflow_id)
        merged = {**self.sanitize_workflow(current), **self.sanitize_workflow(payload)}
        return self.request("PUT", f"/workflows/{workflow_id}", json=merged)

    def delete_workflow(self, workflow_id: str) -> Any:
        return self.request("DELETE", f"/workflows/{workflow_id}")

    def activate_workflow(self, workflow_id: str) -> dict:
        return self.request("POST", f"/workflows/{workflow_id}/activate")

    def deactivate_workflow(self, workflow_id: str) -> dict:
        return self.request("POST", f"/workflows/{workflow_id}/deactivate")

    def list_executions(self, *, workflow_id: Optional[str] = None,
                        status: Optional[str] = None, limit: int = 10,
                        include_data: bool = False) -> list[dict]:
        params: dict[str, Any] = {"limit": max(1, min(limit, 100))}
        if workflow_id:
            params["workflowId"] = workflow_id
        if status:
            params["status"] = status
        if include_data:
            params["includeData"] = "true"
        data = self.get("/executions", params=params)
        return data.get("data", []) if isinstance(data, dict) else []

    def get_execution(self, execution_id: str, include_data: bool = False) -> dict:
        params = {"includeData": "true"} if include_data else None
        return self.get(f"/executions/{execution_id}", params=params)

    def list_credentials(self, *, limit: int = 100) -> list[dict]:
        """列出实例已配置的凭据（仅元信息 id/name/type，公共 API 不回密文）。

        部分 n8n 版本的公共 API 不支持 GET /credentials —— 由调用方兜异常降级。"""
        data = self.get("/credentials", params={"limit": max(1, min(limit, 250))})
        return data.get("data", []) if isinstance(data, dict) else []

    # ── Webhook 触发（平台调度 n8n 流水线的入口） ─────────────────────

    @property
    def instance_root(self) -> str:
        """n8n 实例根地址（api_base 去掉 /api/v1）。"""
        base = self.api_base
        return base[: -len("/api/v1")] if base.endswith("/api/v1") else base

    def trigger_webhook(self, webhook_path: str, payload: Any = None,
                        timeout_seconds: Optional[float] = None) -> tuple[int, Any]:
        """POST 生产 webhook（仅激活的工作流会响应）。返回 (status_code, body)。"""
        url = f"{self.instance_root}/webhook/{webhook_path.lstrip('/')}"
        with httpx.Client(timeout=timeout_seconds or float(self.timeout_seconds)) as client:
            resp = client.post(url, json=payload if payload is not None else {})
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
        return resp.status_code, body

    def test_connection(self) -> N8nConnectionResult:
        data = self.get("/workflows", params={"limit": 1})
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise N8nApiError(
                502,
                "n8n API response is not a valid workflow list; please check the API URL and API key",
                data,
            )
        return N8nConnectionResult(
            ok=True,
            message="n8n connection successful",
            api_base=self.api_base,
        )


def normalize_n8n_api_base(raw: str) -> str:
    """Normalize either an n8n root URL or an /api/v1 URL to the public API base."""
    url = raw.strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    if not url:
        return ""
    if not url.endswith("/api/v1"):
        url = f"{url}/api/v1"
    return url


def test_n8n_connection(api_url: str, api_key: str, timeout_seconds: int = 10) -> N8nConnectionResult:
    client = N8nClient(api_url=api_url, api_key=api_key, timeout_seconds=timeout_seconds)
    return client.test_connection()
