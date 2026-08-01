"""Cypher 只读校验 — 词边界 + ontology_id 隔离"""
import pytest

from app.services.v2.graph.cypher_builder import validate_readonly_cypher


def test_blocks_write_keywords():
    assert validate_readonly_cypher("CREATE (n:X) RETURN n") is not None
    assert validate_readonly_cypher("MATCH (n) DETACH DELETE n") is not None
    assert validate_readonly_cypher("match (n) set n.x = 1 return n") is not None
    assert validate_readonly_cypher("LOAD CSV FROM 'file:///x' AS row RETURN row") is not None


def test_substring_keywords_not_false_positive():
    """属性名含 SET/DROP 子串不应被误拦"""
    q = "MATCH (n) WHERE n.ontology_id = $ontology_id AND n.asset_id = 'A1' RETURN n.backdrop"
    assert validate_readonly_cypher(q) is None


def test_requires_ontology_id_filter():
    err = validate_readonly_cypher("MATCH (n) RETURN n LIMIT 10")
    assert err is not None and "ontology_id" in err


def test_rejects_ontology_id_text_that_is_not_a_bound_property_filter():
    bypass = "MATCH (n) WHERE 'ontology_id' = 'ontology_id' RETURN n"
    assert validate_readonly_cypher(bypass) is not None


def test_valid_scoped_query_passes():
    q = "MATCH (n) WHERE n.ontology_id = $ontology_id RETURN n LIMIT 25"
    assert validate_readonly_cypher(q) is None


def test_every_node_and_relationship_alias_must_be_scoped():
    bypass = (
        "MATCH (n)-[r]->(m) "
        "WHERE n.ontology_id = $ontology_id AND m.ontology_id = $ontology_id "
        "RETURN r"
    )
    error = validate_readonly_cypher(bypass)
    assert error is not None and "r" in error

    scoped = (
        "MATCH (n)-[r]->(m) "
        "WHERE n.ontology_id = $ontology_id "
        "AND r.ontology_id = $ontology_id "
        "AND m.ontology_id = $ontology_id RETURN n, r, m"
    )
    assert validate_readonly_cypher(scoped) is None


def test_inline_pattern_scope_is_accepted_for_every_alias():
    query = (
        "MATCH (n:OntologyEntity {ontology_id: $ontology_id})"
        "-[r:OWNS {ontology_id: $ontology_id}]->"
        "(m:OntologyEntity {ontology_id: $ontology_id}) RETURN n, r, m"
    )
    assert validate_readonly_cypher(query) is None


def test_inline_scope_must_be_the_exact_parameter_value():
    assert validate_readonly_cypher(
        "MATCH (n {ontology_id: $ontology_id + '-other'}) RETURN n"
    ) is not None
    assert validate_readonly_cypher(
        "MATCH (n {ontology_id: $ontology_id, ontology_id: 'other'}) RETURN n"
    ) is not None


@pytest.mark.parametrize(
    "bypass",
    [
        (
            "MATCH (n) WHERE n.ontology_id = $ontology_id "
            "MATCH (m) RETURN m"
        ),
        (
            "MATCH (n) WHERE n.ontology_id = $ontology_id "
            "WITH n MATCH (m) RETURN m"
        ),
        (
            "MATCH (n) WHERE n.ontology_id = $ontology_id "
            "CALL { MATCH (m) RETURN m } RETURN n"
        ),
        (
            "MATCH (n) WHERE n.ontology_id = $ontology_id RETURN n "
            "UNION MATCH (m) RETURN m"
        ),
    ],
)
def test_rejects_query_pipelines_and_secondary_matches(bypass):
    assert validate_readonly_cypher(bypass) is not None


def test_rejects_unscoped_alias_in_the_same_match():
    bypass = (
        "MATCH (n), (m) WHERE n.ontology_id = $ontology_id RETURN n, m"
    )
    error = validate_readonly_cypher(bypass)
    assert error is not None and "m" in error


def test_scope_must_be_a_standalone_conjunct_not_a_tautology_or_projection():
    assert validate_readonly_cypher(
        "MATCH (n) WHERE n.ontology_id = $ontology_id OR true RETURN n"
    ) is not None
    assert validate_readonly_cypher(
        "MATCH (n) RETURN n, n.ontology_id = $ontology_id AS looks_scoped"
    ) is not None


def test_rejects_pattern_comprehension_after_the_audited_match():
    bypass = (
        "MATCH (n) WHERE n.ontology_id = $ontology_id "
        "RETURN [(n)-->(m) | m]"
    )
    assert validate_readonly_cypher(bypass) is not None


def test_rejects_dynamic_apoc_cypher_function():
    bypass = (
        "MATCH (n) WHERE n.ontology_id = $ontology_id "
        "RETURN apoc.cypher.runFirstColumn($query, {}, true)"
    )
    assert validate_readonly_cypher(bypass) is not None


def test_keywords_inside_literals_and_comments_do_not_change_structure():
    query = (
        "MATCH (n) WHERE n.ontology_id = $ontology_id "
        "AND n.note = 'CALL MATCH UNION CREATE' RETURN n "
        "// MATCH (m) RETURN m"
    )
    assert validate_readonly_cypher(query) is None
