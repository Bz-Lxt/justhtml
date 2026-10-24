"""Normalization and merging of attribute name and value allowlists."""

from __future__ import annotations

from collections.abc import Collection, Mapping

from .values import Regex, _value_rules_signature, _ValueRules

_AttributeRules = Collection[str] | Mapping[str, _ValueRules]
_AllowedAttributes = Mapping[str, _AttributeRules]


def _normalize_allowed_attributes(attributes: _AllowedAttributes) -> dict[str, _AttributeRules]:
    normalized: dict[str, _AttributeRules] = {}
    for tag, attrs in attributes.items():
        name = str(tag).strip().lower()
        if not name:
            raise ValueError("allowed_attributes contains an empty tag key")
        if isinstance(attrs, str):
            raise TypeError("allowed_attributes values must be collections of names or mappings to value rules")
        if isinstance(attrs, Mapping):
            rules: dict[str, _ValueRules] = {}
            for attr, values in attrs.items():
                if isinstance(values, str) or any(not isinstance(v, (str, Regex)) for v in values):
                    raise TypeError("Attribute value rules must be collections of strings or Regex objects")
                key = str(attr).strip().lower()
                if key:
                    rules[key] = frozenset(rules.get(key, ())).union(values)
            previous = normalized.get(name, {})
            if not isinstance(previous, Mapping):
                raise TypeError("Cannot mix name-only and value rules for the same normalized tag")
            normalized[name] = {
                **previous,
                **{key: frozenset(previous.get(key, ())).union(values) for key, values in rules.items()},
            }
        else:
            names = {str(attr).strip().lower() for attr in attrs if str(attr).strip()}
            previous = normalized.get(name, set())
            if isinstance(previous, Mapping):
                raise TypeError("Cannot mix name-only and value rules for the same normalized tag")
            normalized[name] = set(previous).union(names)
    return normalized


def _attribute_rules_with_values(attributes: _AttributeRules) -> dict[str, _ValueRules | None]:
    if isinstance(attributes, Mapping):
        return dict(attributes)
    return dict.fromkeys(attributes)


def _merge_attribute_value_rules(
    global_rules: Mapping[str, _ValueRules | None],
    local: _AttributeRules,
) -> dict[str, _ValueRules | None]:
    merged = dict(global_rules)
    for attr, rules in _attribute_rules_with_values(local).items():
        if attr not in merged:
            merged[attr] = rules
        else:
            previous = merged[attr]
            merged[attr] = None if previous is None or rules is None else frozenset(previous).union(rules)
    return merged


def _allowed_attributes_signature(attributes: _AllowedAttributes) -> tuple[object, ...]:
    return tuple(
        (
            tag,
            tuple(
                (attr, None if rules is None else _value_rules_signature(rules))
                for attr, rules in sorted(_attribute_rules_with_values(attrs).items())
            ),
        )
        for tag, attrs in sorted(attributes.items())
    )
