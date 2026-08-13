"""Skill 渐进披露的内置工具：schema 与执行器。

独立成模块是为了让 runtime 与 subagent 共用而不产生依赖环
（subagent 只允许访问这两个只读 Skill 工具）。
"""
from __future__ import annotations

import json
from typing import Any

from app.super_assistant.models import SuperAssistantSkill
from app.super_assistant.skill_store import read_text_file, skill_directory


def builtin_skill_tool_schemas() -> list[dict[str, Any]]:
    """use_skill / read_skill_file 两个只读工具的声明。"""
    return [
        {
            "name": "use_skill",
            "description": "读取一个相关 Skill 的完整 SKILL.md 指令和目录清单。",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Skill name"}},
                "required": ["name"],
                "additionalProperties": False,
            },
        },
        {
            "name": "read_skill_file",
            "description": "读取 Skill 目录内的一个 UTF-8 文本文件，例如 references/example.md 或 scripts/tool.py。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name"},
                    "path": {"type": "string", "description": "相对于 Skill 根目录的文件路径"},
                },
                "required": ["name", "path"],
                "additionalProperties": False,
            },
        },
    ]


def execute_skill_tool(db, owner_id: str, name: str, arguments: dict[str, Any]) -> str:
    skill_name = str(arguments.get("name") or "")
    skill = db.query(SuperAssistantSkill).filter(
        SuperAssistantSkill.owner_id == owner_id,
        SuperAssistantSkill.name == skill_name,
        SuperAssistantSkill.enabled.is_(True),
    ).first()
    if not skill:
        return json.dumps({"error": f"Skill {skill_name!r} 不存在或未启用"}, ensure_ascii=False)
    folder = skill_directory(owner_id, skill.id)
    if name == "use_skill":
        content = read_text_file(folder, "SKILL.md")
        return json.dumps({"skill": skill.name, "skill_md": content, "files": skill.manifest}, ensure_ascii=False)
    path = str(arguments.get("path") or "")
    content = read_text_file(folder, path)
    return json.dumps({"skill": skill.name, "path": path, "content": content}, ensure_ascii=False)
