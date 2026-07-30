from app.exploration.skills import exploration_skills


def test_diagram_skills_require_the_deterministic_tool_path():
    skills = exploration_skills()

    for name in ("er_diagram", "business_flowchart"):
        instructions = skills[name].instructions
        assert "show_diagram" in instructions
        assert "不能由模型手写 Mermaid" in instructions
        assert "自行输出 Mermaid 代码块" in instructions
