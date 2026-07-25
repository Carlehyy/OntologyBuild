from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
INDEX_SETUP = ROOT / "backend" / "app" / "ontologies" / "graph" / "index_setup.py"


def _index_statements() -> list[str]:
    tree = ast.parse(INDEX_SETUP.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "INDEXES"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("INDEXES definition not found")


def test_property_indexes_are_scoped_to_a_node_label():
    statements = _index_statements()

    assert statements
    assert all(" FOR (n:" in statement for statement in statements)
    assert all(" FOR (n) ON (" not in statement for statement in statements)
