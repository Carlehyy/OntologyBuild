"""Canonical API Hub interface request contracts."""
from __future__ import annotations

import re
from typing import Any, List, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from . import config


_ALLOWED_METHODS = {
    "GET",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "HEAD",
    "OPTIONS",
}
_ALLOWED_BODY_TYPES = {"none", "json", "form", "multipart", "raw"}
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


class KV(BaseModel):
    key: str = ""
    value: str = ""


class FileField(BaseModel):
    key: str = ""
    accept: str = ""
    multiple: bool = False


class InterfaceParameter(BaseModel):
    """Machine-readable contract used by Agents and n8n bindings.

    Saved request values remain the source of defaults.  This schema describes
    which values callers may provide dynamically without exposing credentials.
    """

    name: str
    location: Literal["path", "query", "header", "body"]
    value_type: Literal[
        "string",
        "integer",
        "number",
        "boolean",
        "object",
        "array",
    ] = "string"
    required: bool = False
    default: Any = None
    description: str = ""
    sensitive: bool = False
    dynamic: bool = True

    @field_validator("name")
    @classmethod
    def validate_parameter_name(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 500 or "\r" in value or "\n" in value:
            raise ValueError("接口参数名无效")
        return value

    @field_validator("description")
    @classmethod
    def validate_parameter_description(cls, value: str) -> str:
        if len(value) > 2000:
            raise ValueError("接口参数说明不能超过 2000 个字符")
        return value


class InterfaceIn(BaseModel):
    name: str = "未命名接口"
    description: str = ""
    group_name: str = ""
    method: str = "GET"
    url: str = ""
    query_params: List[KV] = Field(default_factory=list)
    headers: List[KV] = Field(default_factory=list)
    body_type: str = "none"   # none | json | form | multipart | raw
    body_content: str = ""
    file_fields: List[FileField] = Field(default_factory=list)
    mcp_enabled: bool = False
    open_enabled: bool = False
    http_enabled: bool = False
    proxy_slug: str = ""
    proxy_query_keys: List[str] = Field(default_factory=list)
    proxy_header_keys: List[str] = Field(default_factory=list)
    proxy_body_enabled: bool = False
    proxy_body_keys: List[str] = Field(default_factory=list)
    parameter_schema: List[InterfaceParameter] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("接口名称不能为空")
        if len(value) > 200:
            raise ValueError("接口名称不能超过 200 个字符")
        return value

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        if len(value) > 20_000:
            raise ValueError("用途说明不能超过 20000 个字符")
        return value

    @field_validator("method")
    @classmethod
    def validate_method(cls, value: str) -> str:
        value = value.upper().strip()
        if value not in _ALLOWED_METHODS:
            raise ValueError("不支持的 HTTP 方法")
        return value

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        value = value.strip()
        parsed = urlsplit(value)
        # mcp-bridge:// 为平台保留方案：接口由执行器进程内分发为服务端
        # MCP 调用（见 api_hub.mcp_bridge），不是出站 HTTP 目标。
        if parsed.scheme.lower() == "mcp-bridge":
            if not parsed.netloc or not (parsed.path or "").lstrip("/"):
                raise ValueError("MCP 桥接地址格式无效，应为 mcp-bridge://<server_id>/<tool_name>")
            return value
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError(
                "请求 URL 必须是无内嵌账号信息的 HTTP/HTTPS 绝对地址",
            )
        if len(value) > 4096:
            raise ValueError("请求 URL 不能超过 4096 个字符")
        return value

    @field_validator("body_type")
    @classmethod
    def validate_body_type(cls, value: str) -> str:
        value = value.lower().strip()
        if value not in _ALLOWED_BODY_TYPES:
            raise ValueError("不支持的请求 Body 类型")
        return value

    @field_validator("body_content")
    @classmethod
    def validate_body_size(cls, value: str) -> str:
        if len(value.encode("utf-8")) > config.PROXY_MAX_REQUEST_BYTES:
            raise ValueError("请求 Body 超过平台允许的最大长度")
        return value

    @field_validator("query_params")
    @classmethod
    def validate_query_params(cls, value: List[KV]) -> List[KV]:
        for item in value:
            if "\r" in item.key or "\n" in item.key:
                raise ValueError("查询参数名不能包含换行符")
            if len(item.key) > 500 or len(item.value) > 100_000:
                raise ValueError("查询参数过长")
        return value

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, value: List[KV]) -> List[KV]:
        for item in value:
            key = item.key.strip()
            if key and not _HEADER_NAME_RE.fullmatch(key):
                raise ValueError(f"Header 名称无效：{key}")
            if "\r" in item.value or "\n" in item.value:
                raise ValueError(f"Header 值不能包含换行符：{key}")
            if len(item.value) > 100_000:
                raise ValueError(f"Header 值过长：{key}")
            item.key = key
        return value

    @field_validator("file_fields")
    @classmethod
    def validate_file_fields(
        cls,
        value: List[FileField],
    ) -> List[FileField]:
        if len(value) > 50:
            raise ValueError("文件字段不能超过 50 个")
        seen = set()
        for item in value:
            key = item.key.strip()
            marker = key.lower()
            if key and not _HEADER_NAME_RE.fullmatch(key):
                raise ValueError(f"文件字段名称无效：{key}")
            if marker and marker in seen:
                raise ValueError(f"文件字段名称重复：{key}")
            if len(item.accept) > 500:
                raise ValueError(f"文件类型限制过长：{key}")
            item.key = key
            item.accept = item.accept.strip()
            if marker:
                seen.add(marker)
        return value

    @field_validator("parameter_schema")
    @classmethod
    def validate_parameter_schema(
        cls,
        value: List[InterfaceParameter],
    ) -> List[InterfaceParameter]:
        if len(value) > 200:
            raise ValueError("接口参数定义不能超过 200 个")
        seen = set()
        for item in value:
            marker = (
                item.location,
                item.name.lower()
                if item.location == "header"
                else item.name,
            )
            if marker in seen:
                raise ValueError(
                    f"接口参数定义重复：{item.location}.{item.name}",
                )
            if item.sensitive and item.dynamic:
                raise ValueError(
                    f"敏感参数不能开放动态覆盖：{item.location}.{item.name}",
                )
            seen.add(marker)
        return value


class PreviewInterfaceIn(InterfaceIn):
    id: int | None = Field(default=None, gt=0)


class DeleteGroupBody(BaseModel):
    group_name: str
