"""OpenAPI contract helpers: parsing, validation and operation extraction."""

from __future__ import annotations

import re
from typing import Any

import yaml
from openapi_spec_validator import validate as validate_openapi

HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})
_PATH_PARAM = re.compile(r"\{[^}]+\}")

Operation = tuple[str, str]  # (METHOD, normalized path)


def contract_problems(text: str) -> list[str]:
    """Return why `text` is not a valid OpenAPI document (empty list when it is valid)."""
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [f"Invalid YAML: {exc}"]
    if not isinstance(doc, dict):
        return ["The contract must be a YAML mapping (an OpenAPI document)."]
    if not doc.get("paths"):
        return ["The contract defines no paths."]
    try:
        validate_openapi(doc)
    except Exception as exc:  # the validator raises several unrelated exception types
        return [f"Invalid OpenAPI document: {str(exc)[:1500]}"]
    return []


def operations(doc: dict[str, Any]) -> set[Operation]:
    """Set of (METHOD, path) pairs, with path parameter names normalized ('/l/{code}' -> '/l/{}')."""
    return {
        (method.upper(), _PATH_PARAM.sub("{}", path))
        for path, item in (doc.get("paths") or {}).items()
        if isinstance(item, dict)
        for method in item
        if method.lower() in HTTP_METHODS
    }
