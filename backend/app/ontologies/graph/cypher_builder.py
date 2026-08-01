"""安全的 Cypher 查询构建器 — 防注入"""
from __future__ import annotations
import re


LABEL_PATTERN = re.compile(r'^[A-Za-z][A-Za-z0-9_]*$')


def validate_label(label: str) -> str:
    """校验 Neo4j 标签名 (防注入)"""
    if not LABEL_PATTERN.match(label):
        raise ValueError(f"Invalid Neo4j label: {label!r}")
    return label


def build_match_by_id(label: str, node_id: str) -> tuple[str, dict]:
    label = validate_label(label)
    return (
        f"MATCH (n:{label} {{id: $id}}) RETURN n",
        {"id": node_id},
    )


def build_neighbors(label: str, node_id: str, depth: int = 1) -> tuple[str, dict]:
    label = validate_label(label)
    depth = max(1, min(depth, 5))  # 最多 5 层
    return (
        f"MATCH (n:{label} {{id: $id}})-[r*1..{depth}]-(m) RETURN n, r, m LIMIT 100",
        {"id": node_id},
    )


def build_shortest_path(src_id: str, tgt_id: str) -> tuple[str, dict]:
    return (
        "MATCH (s {id: $src}), (t {id: $tgt}), p = shortestPath((s)-[*]-(t)) RETURN p",
        {"src": src_id, "tgt": tgt_id},
    )


_WRITE_KEYWORD = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|INSERT|LOAD\s+CSV)\b",
    re.IGNORECASE,
)
_UNSAFE_QUERY_CLAUSE = re.compile(
    r"\b(OPTIONAL|WITH|CALL|UNION|UNWIND|FOREACH|USE|YIELD|SHOW|"
    r"TERMINATE|TRANSACTIONS|PROFILE|EXPLAIN|CYPHER|LET|FINISH)\b",
    re.IGNORECASE,
)
_UNSAFE_BOOLEAN_OPERATOR = re.compile(
    r"\b(OR|XOR|NOT|CASE|WHEN|THEN|ELSE)\b",
    re.IGNORECASE,
)
_NODE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])\(\s*(?P<alias>[A-Za-z_][A-Za-z0-9_]*)\b"
    r"(?P<body>[^()]*)\)",
)
_RELATIONSHIP_PATTERN = re.compile(r"\[(?P<body>[^\[\]]*)\]")
_INLINE_SCOPE = re.compile(
    r"\{[^{}]*\bontology_id\s*:\s*\$ontology_id\s*(?=,|\})[^{}]*\}",
    re.IGNORECASE,
)
_SCOPE_MAP_KEY = re.compile(r"\bontology_id\s*:", re.IGNORECASE)
_DYNAMIC_CYPHER_FUNCTION = re.compile(
    r"\bapoc\s*\.\s*cypher\s*\.",
    re.IGNORECASE,
)


def _mask_cypher_literals_and_comments(query: str) -> tuple[str, str | None]:
    """Mask text that cannot contain Cypher structure.

    Structural checks must not accept a fake scope hidden in a comment, nor
    reject a harmless keyword inside a business string. Keeping punctuation and
    length stable lets the later clause slicing operate on the original query.
    """
    chars = list(query)
    masked = list(query)
    index = 0
    length = len(chars)
    while index < length:
        char = chars[index]
        next_char = chars[index + 1] if index + 1 < length else ""
        if char == "/" and next_char == "/":
            masked[index] = masked[index + 1] = " "
            index += 2
            while index < length and chars[index] not in "\r\n":
                masked[index] = " "
                index += 1
            continue
        if char == "/" and next_char == "*":
            masked[index] = masked[index + 1] = " "
            index += 2
            while index + 1 < length and not (
                chars[index] == "*" and chars[index + 1] == "/"
            ):
                masked[index] = " "
                index += 1
            if index + 1 >= length:
                return "".join(masked), "Unterminated Cypher block comment"
            masked[index] = masked[index + 1] = " "
            index += 2
            continue
        if char in {"'", '"', "`"}:
            quote = char
            masked[index] = " "
            index += 1
            while index < length:
                masked[index] = " "
                if chars[index] == "\\":
                    if index + 1 < length:
                        masked[index + 1] = " "
                        index += 2
                        continue
                if chars[index] == quote:
                    # Cypher accepts doubled quotes/backticks as an escaped
                    # delimiter. Keep both masked and continue the literal.
                    if index + 1 < length and chars[index + 1] == quote:
                        masked[index + 1] = " "
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            else:
                return "".join(masked), "Unterminated Cypher literal"
            continue
        index += 1
    return "".join(masked), None


def _strip_balanced_outer_parentheses(expression: str) -> str:
    result = expression.strip()
    while result.startswith("(") and result.endswith(")"):
        depth = 0
        encloses_all = True
        for index, char in enumerate(result):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth < 0:
                    return result
                if depth == 0 and index != len(result) - 1:
                    encloses_all = False
                    break
        if depth != 0 or not encloses_all:
            break
        result = result[1:-1].strip()
    return result


def _top_level_and_terms(expression: str) -> list[str]:
    """Flatten a WHERE expression whose only boolean join is ``AND``."""
    expression = _strip_balanced_outer_parentheses(expression)
    depth = 0
    start = 0
    terms: list[str] = []
    index = 0
    while index < len(expression):
        char = expression[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return []
        elif depth == 0:
            match = re.match(r"\bAND\b", expression[index:], re.IGNORECASE)
            if match:
                terms.extend(_top_level_and_terms(expression[start:index]))
                index += match.end()
                start = index
                continue
        index += 1
    if depth != 0:
        return []
    tail = expression[start:].strip()
    if tail:
        stripped = _strip_balanced_outer_parentheses(tail)
        if stripped != tail:
            terms.extend(_top_level_and_terms(stripped))
        else:
            terms.append(tail)
    return terms


def _where_scoped_aliases(expression: str) -> set[str]:
    scoped: set[str] = set()
    for raw_term in _top_level_and_terms(expression):
        term = _strip_balanced_outer_parentheses(raw_term)
        match = re.fullmatch(
            r"(?P<alias>[A-Za-z_][A-Za-z0-9_]*)\.ontology_id\s*=\s*"
            r"\$ontology_id|\$ontology_id\s*=\s*"
            r"(?P<reverse>[A-Za-z_][A-Za-z0-9_]*)\.ontology_id",
            term,
            flags=re.IGNORECASE,
        )
        if match:
            scoped.add(match.group("alias") or match.group("reverse"))
    return scoped


def _pattern_bindings(match_pattern: str) -> tuple[dict[str, bool], str | None]:
    """Return graph aliases and whether each is scoped in its pattern map."""
    node_matches = list(_NODE_PATTERN.finditer(match_pattern))
    if (
        match_pattern.count("(") != len(node_matches)
        or match_pattern.count(")") != len(node_matches)
        or not node_matches
    ):
        return {}, "MATCH must use explicit, non-nested node aliases"

    relationship_matches = list(_RELATIONSHIP_PATTERN.finditer(match_pattern))
    if (
        match_pattern.count("[") != len(relationship_matches)
        or match_pattern.count("]") != len(relationship_matches)
    ):
        return {}, "MATCH contains an unsupported relationship pattern"
    if re.search(r"--|<-\s*-|-\s*->", match_pattern):
        return {}, "Anonymous relationships are not allowed"

    bindings: dict[str, bool] = {}
    for match in node_matches:
        alias = match.group("alias")
        body = match.group("body")
        if len(_SCOPE_MAP_KEY.findall(body)) > 1:
            return {}, f"Graph alias {alias} declares ontology_id more than once"
        scoped = _INLINE_SCOPE.search(body) is not None
        bindings[alias] = bindings.get(alias, False) or scoped

    for match in relationship_matches:
        body = match.group("body").strip()
        alias_match = re.match(r"(?P<alias>[A-Za-z_][A-Za-z0-9_]*)\b", body)
        if alias_match is None:
            return {}, "Relationships must use explicit aliases"
        if "*" in body:
            return {}, "Variable-length relationships are not allowed"
        alias = alias_match.group("alias")
        if len(_SCOPE_MAP_KEY.findall(body)) > 1:
            return {}, f"Graph alias {alias} declares ontology_id more than once"
        scoped = _INLINE_SCOPE.search(body) is not None
        bindings[alias] = bindings.get(alias, False) or scoped
    return bindings, None


def validate_readonly_cypher(query: str) -> str | None:
    """Validate one provably ontology-scoped, read-only Cypher MATCH.

    This endpoint deliberately accepts a conservative subset instead of trying
    to parse all of Cypher with regular expressions. A query may contain one
    ``MATCH`` and one ``RETURN`` only. Every named node and relationship must be
    constrained either in its pattern map or by a standalone top-level ``AND``
    term in ``WHERE``. Query pipelines, subqueries and secondary graph patterns
    are rejected because they can introduce an unscoped variable after an
    otherwise legitimate predicate.
    """
    masked, masking_error = _mask_cypher_literals_and_comments(query)
    if masking_error:
        return masking_error
    if ";" in masked:
        return "Multiple Cypher statements are not allowed"

    m = _WRITE_KEYWORD.search(masked)
    if m:
        return f"Write queries not allowed via this endpoint: {m.group(1).upper()}"

    unsafe_clause = _UNSAFE_QUERY_CLAUSE.search(masked)
    if unsafe_clause:
        return (
            "Unsupported Cypher clause in ontology-scoped query: "
            f"{unsafe_clause.group(1).upper()}"
        )
    if _DYNAMIC_CYPHER_FUNCTION.search(masked):
        return "Dynamic Cypher functions are not allowed"

    match_clauses = list(re.finditer(r"\bMATCH\b", masked, re.IGNORECASE))
    return_clauses = list(re.finditer(r"\bRETURN\b", masked, re.IGNORECASE))
    if len(match_clauses) != 1 or len(return_clauses) != 1:
        return "Query must contain exactly one MATCH and one RETURN clause"
    match_clause = match_clauses[0]
    return_clause = return_clauses[0]
    if masked[:match_clause.start()].strip() or return_clause.start() <= match_clause.end():
        return "Query must start with MATCH and end through its single RETURN"

    between = masked[match_clause.end():return_clause.start()]
    where_clauses = list(re.finditer(r"\bWHERE\b", between, re.IGNORECASE))
    if len(where_clauses) > 1:
        return "Query may contain at most one WHERE clause"
    if where_clauses:
        where_clause = where_clauses[0]
        match_pattern = between[:where_clause.start()].strip()
        where_expression = between[where_clause.end():].strip()
        query_tail = between[where_clause.start():] + masked[return_clause.start():]
        if not where_expression:
            return "WHERE clause cannot be empty"
        if _UNSAFE_BOOLEAN_OPERATOR.search(where_expression):
            return "WHERE scope cannot be weakened by OR/NOT/CASE expressions"
    else:
        match_pattern = between.strip()
        where_expression = ""
        query_tail = masked[return_clause.start():]

    # No pattern comprehension, shortestPath expansion or legacy EXISTS
    # pattern may introduce additional graph entities after the audited MATCH.
    if re.search(r"-\s*\[|\]\s*-|-->|<--", query_tail):
        return "Graph patterns outside the single MATCH clause are not allowed"

    bindings, pattern_error = _pattern_bindings(match_pattern)
    if pattern_error:
        return pattern_error
    where_scoped = _where_scoped_aliases(where_expression)
    missing = sorted(
        alias
        for alias, inline_scoped in bindings.items()
        if not inline_scoped and alias not in where_scoped
    )
    if missing:
        return (
            "Every graph alias must be constrained by $ontology_id; missing: "
            + ", ".join(missing)
        )
    return None
