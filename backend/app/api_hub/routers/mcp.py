import socket

from fastapi import APIRouter

from .. import config, mcp_server

router = APIRouter(prefix="/mcp", tags=["api-hub-mcp"])


def _lan_ip() -> str:
    """尽力探测本机在局域网中的 IP（不真正发包，仅用于拼出可被其它电脑访问的地址）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    try:
        host = socket.gethostbyname(socket.gethostname())
        if host and not host.startswith("127."):
            return host
    except OSError:
        pass
    return ""


@router.get("/info")
def info():
    return {
        "endpoint": config.MCP_PATH,
        "host": config.APP_HOST,
        "port": config.APP_PORT,
        "lan_ip": _lan_ip(),
        "server_name": config.MCP_SERVER_NAME,
        "transport": "streamable-http",
        "lan_exposed": config.is_lan_exposed(),
        "token_required": bool(config.MCP_TOKEN),
        "token": config.MCP_TOKEN,
        "published": mcp_server.published_tools(),
    }


@router.get("/system/info")
def system_info():
    """系统管理 MCP 的配置信息（供设置弹窗展示）。"""
    return {
        "endpoint": config.SYSTEM_MCP_PATH,
        "host": config.APP_HOST,
        "port": config.APP_PORT,
        "lan_ip": _lan_ip(),
        "server_name": config.MCP_SERVER_NAME + "-system",
        "transport": "streamable-http",
        "lan_exposed": config.is_lan_exposed(),
        "token_required": bool(config.SYSTEM_MCP_TOKEN),
        "token": config.SYSTEM_MCP_TOKEN,
        "tools": [
            {"name": "list_groups", "desc": "列出所有分组及接口数量"},
            {"name": "list_interfaces", "desc": "列出接口（可按分组筛选）"},
            {"name": "get_interface", "desc": "获取单个接口完整信息"},
            {"name": "create_interface", "desc": "新建接口（支持 query_params / headers）"},
            {"name": "update_interface", "desc": "更新已有接口（支持 query_params / headers）"},
            {"name": "delete_interface", "desc": "删除接口"},
            {"name": "delete_group", "desc": "删除分组（接口移入默认分组）"},
            {"name": "call_interface", "desc": "调用接口并返回响应"},
        ],
    }
