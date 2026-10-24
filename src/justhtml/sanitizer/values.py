"""Literal and regular-expression rules for trusted sanitization policies."""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Regex:
    """A trusted whole-value regular expression in a sanitization allowlist."""

    pattern: str
    _compiled: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_compiled", re.compile(self.pattern))


_ValueRules = Collection[str | Regex]


def _matches_value_rules(rules: _ValueRules, value: str) -> bool:
    return any(
        rule._compiled.fullmatch(value) is not None if isinstance(rule, Regex) else value == rule for rule in rules
    )


def _value_rules_signature(rules: _ValueRules) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(("regex", rule.pattern) if isinstance(rule, Regex) else ("literal", rule) for rule in rules))
