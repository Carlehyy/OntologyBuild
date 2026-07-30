"""Application operations for editable extraction rules."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy.orm import Session

from app.settings.rules.models import RulesConfig


def get_rules(db: Session) -> dict[str, list[dict[str, Any]]]:
    rules = db.query(RulesConfig).order_by(RulesConfig.rule_key).all()
    return {
        "data": [
            {
                "id": rule.id,
                "rule_key": rule.rule_key,
                "rule_value": rule.rule_value,
                "rule_label_cn": rule.rule_label_cn,
                "rule_label_en": rule.rule_label_en,
                "editable": rule.editable,
            }
            for rule in rules
        ],
    }


def update_rules(
    updates: Iterable[Any],
    db: Session,
) -> dict[str, str]:
    for update in updates:
        rule = (
            db.query(RulesConfig)
            .filter(
                RulesConfig.rule_key == update.rule_key,
                RulesConfig.editable == True,  # noqa: E712
            )
            .first()
        )
        if rule:
            rule.rule_value = update.rule_value
    db.commit()
    return {"message": "Rules updated"}
