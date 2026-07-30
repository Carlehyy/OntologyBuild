"""Compatibility and dependency contracts for the formal Action engine."""
from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path
from types import SimpleNamespace

from app.ontologies.formal_modeling import action_definition_validation
from app.ontologies.formal_modeling import action_effect_persistence
from app.ontologies.formal_modeling import action_effects
from app.ontologies.formal_modeling import action_engine
from app.ontologies.formal_modeling import action_execution_errors
from app.ontologies.formal_modeling import action_execution_records
from app.ontologies.formal_modeling import action_notification_effect
from app.ontologies.formal_modeling import action_runtime_contracts
from app.ontologies.formal_modeling import action_runtime_support
from app.ontologies.formal_modeling import action_runtime_values
from app.ontologies.formal_modeling import action_validation
from app.services.formal import action_engine as compatibility_action_engine


def test_public_action_symbols_keep_canonical_object_identity():
    assert (
        action_engine.prepare_action_parameters
        is action_validation.prepare_action_parameters
    )
    assert (
        action_engine.validate_action_definition
        is action_validation.validate_action_definition
    )
    assert (
        action_engine.action_supports_snapshot_execution
        is action_validation.action_supports_snapshot_execution
    )
    assert (
        action_engine.RuleExecutionError
        is action_runtime_support.RuleExecutionError
        is action_execution_errors.RuleExecutionError
    )
    assert (
        action_engine._log_to_dict
        is action_runtime_support._log_to_dict
        is action_execution_records._log_to_dict
    )
    assert (
        action_runtime_support._resolve_value
        is action_runtime_values._resolve_value
    )
    assert (
        action_runtime_support._validate_object_write
        is action_runtime_contracts._validate_object_write
    )
    assert (
        action_validation.validate_action_definition
        is action_definition_validation.validate_action_definition
    )
    assert compatibility_action_engine.execute_action is action_engine.execute_action
    assert (
        compatibility_action_engine.prepare_action_parameters
        is action_engine.prepare_action_parameters
    )
    assert (
        compatibility_action_engine.validate_action_definition
        is action_engine.validate_action_definition
    )


def test_runtime_support_reexports_every_compatibility_helper():
    providers = {
        action_execution_records: {
            "_current_release_error",
            "_fail_log",
            "_failed_effects",
            "_idempotency_key",
            "_idempotency_owner",
            "_idempotent_replay",
            "_is_executing_sentinel_approval",
            "_log_to_dict",
            "_match_state_id",
            "_normalize_target_snapshot",
            "_now",
            "_rule_identity",
            "_same_idempotent_request",
            "_sentinel_id_from_execution_lineage",
        },
        action_runtime_contracts: {
            "_contract_messages",
            "_runtime_instance_query",
            "_runtime_link_query",
            "_validate_link_candidate",
            "_validate_link_write",
            "_validate_object_candidate",
            "_validate_object_write",
        },
        action_runtime_values: {
            "_evaluate_context_derived_projection",
            "_execute_action_function",
            "_preview_find",
            "_preview_instance_values",
            "_preview_link_values",
            "_preview_values",
            "_render_template",
            "_resolve_recipient",
            "_resolve_value",
            "_validate",
        },
    }
    for provider, names in providers.items():
        for name in names:
            assert getattr(action_runtime_support, name) is getattr(
                provider,
                name,
            )


def test_execute_action_resolves_locked_entrypoint_at_call_time(monkeypatch):
    marker = {"status": "compatibility-entrypoint"}
    received: dict = {}

    def replacement(*args, **kwargs):
        received["args"] = args
        received["kwargs"] = kwargs
        return marker

    monkeypatch.setattr(action_engine, "_execute_action_locked", replacement)
    body = SimpleNamespace(preview_only=True)

    result = action_engine.execute_action(
        None,
        "isolated-preview",
        body,
        preview_only=True,
        preview_context={"isolated": True},
    )

    assert result is marker
    assert received["args"] == (None, "isolated-preview", body)
    assert received["kwargs"]["preview_only"] is True
    assert received["kwargs"]["preview_context"] == {"isolated": True}


def test_locked_entrypoint_is_named_phase_orchestration_only():
    source = textwrap.dedent(inspect.getsource(
        action_engine._execute_action_locked))
    tree = ast.parse(source)
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert {
        "_resolve_action_execution_definition",
        "_prepare_action_execution_request",
        "_execute_action_effects",
        "_finalize_action_execution",
    } <= calls
    assert len(source.splitlines()) <= 80
    assert "db.commit" not in source
    assert "for rule" not in source


def test_action_layers_do_not_import_the_canonical_facade():
    package_dir = Path(action_engine.__file__).resolve().parent
    layer_names = {
        "action_definition_validation",
        "action_effect_persistence",
        "action_validation",
        "action_execution_context",
        "action_execution_errors",
        "action_execution_records",
        "action_notification_effect",
        "action_runtime_contracts",
        "action_runtime_support",
        "action_runtime_values",
        "action_effects",
    }
    for layer_name in layer_names:
        tree = ast.parse(
            (package_dir / f"{layer_name}.py").read_text(encoding="utf-8"))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        imported_modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert (
            "app.ontologies.formal_modeling.action_engine"
            not in imported_modules
        )

    assert action_engine._execute_action_effects is action_effects._execute_action_effects
    assert (
        action_engine._finalize_action_execution
        is action_effects._finalize_action_execution
    )


def test_definition_validation_is_rule_handler_orchestration():
    source = textwrap.dedent(inspect.getsource(
        action_definition_validation.validate_action_definition))
    assert len(source.splitlines()) <= 20

    validator = action_definition_validation._ActionDefinitionValidator
    handlers = {
        "_validate_validation_rule",
        "_validate_create_object_rule",
        "_validate_update_property_rule",
        "_validate_link_rule",
        "_validate_notification_rule",
        "_validate_webhook_rule",
    }
    assert handlers <= set(vars(validator))
    assert max(
        len(inspect.getsource(getattr(validator, name)).splitlines())
        for name in handlers
    ) <= 90


def test_effect_interpreter_delegates_independent_effect_responsibilities():
    source = textwrap.dedent(inspect.getsource(
        action_effects._execute_action_effects))
    tree = ast.parse(source)
    names = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    assert {
        "_execute_internal_notification",
        "_record_and_recompute",
        "_preview_derived_projection",
    } <= names
    assert len(source.splitlines()) <= 650

    persistence_source = inspect.getsource(action_effect_persistence)
    notification_source = inspect.getsource(action_notification_effect)
    assert "db.commit" not in persistence_source
    assert "db.rollback" not in persistence_source
    assert "db.commit" not in notification_source
    assert "db.rollback" not in notification_source
