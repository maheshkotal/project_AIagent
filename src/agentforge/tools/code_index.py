"""Static code index (Python AST): modules, symbols, signatures, HTTP routes and the import graph.

Gives the impact analyst a deterministic, system-level map of an existing codebase instead of
relying on the LLM to infer structure from raw text.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from agentforge.tools.sandbox import Sandbox

ROUTE_METHODS = frozenset({"get", "post", "put", "patch", "delete"})


@dataclass
class ModuleInfo:
    path: str
    module: str
    summary: str = ""
    symbols: list[str] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)
    imports: set[str] = field(default_factory=set)


def _module_name(path: str) -> str:
    parts = PurePosixPath(path).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{prefix} {node.name}({ast.unparse(node.args)}){returns}"


def _routes(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    routes = []
    for deco in node.decorator_list:
        if (
            isinstance(deco, ast.Call)
            and isinstance(deco.func, ast.Attribute)
            and deco.func.attr in ROUTE_METHODS
            and deco.args
            and isinstance(deco.args[0], ast.Constant)
        ):
            routes.append(f"{deco.func.attr.upper()} {deco.args[0].value} -> {node.name}()")
    return routes


def index_module(path: str, source: str, local_packages: set[str]) -> ModuleInfo:
    info = ModuleInfo(path=path, module=_module_name(path))
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        info.summary = f"(syntax error: {exc.msg})"
        return info
    docstring = ast.get_docstring(tree)
    info.summary = docstring.strip().splitlines()[0] if docstring else ""
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            info.symbols.append(_signature(node))
            info.routes += _routes(node)
        elif isinstance(node, ast.ClassDef):
            bases = f"({', '.join(ast.unparse(b) for b in node.bases)})" if node.bases else ""
            info.symbols.append(f"class {node.name}{bases}")
            info.symbols += [
                f"    {_signature(item)}"
                for item in node.body
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
                and (not item.name.startswith("_") or item.name == "__init__")
            ]
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module.split(".")[0] in local_packages:
                info.imports.add(node.module)
        elif isinstance(node, ast.Import):
            info.imports |= {a.name for a in node.names if a.name.split(".")[0] in local_packages}
    return info


def repo_map(ws: Sandbox) -> str:
    """Markdown map of the codebase: per module its purpose, public symbols, routes, imports and importers."""
    files = ws.list_files()
    local = {PurePosixPath(p).parts[0].removesuffix(".py") for p in files}
    modules = [index_module(p, ws.read_text(p), local) for p in files if p.endswith(".py")]
    importers: dict[str, list[str]] = {}
    for m in modules:
        for imported in m.imports:
            importers.setdefault(imported, []).append(m.module)

    blocks = []
    for m in modules:
        lines = [f"### `{m.path}`" + (f" - {m.summary}" if m.summary else "")]
        if m.routes:
            lines.append("routes: " + "; ".join(m.routes))
        if m.imports:
            lines.append("imports: " + ", ".join(sorted(m.imports)))
        if importers.get(m.module):
            lines.append("imported by: " + ", ".join(sorted(importers[m.module])))
        lines += [f"    {s}" for s in m.symbols]
        blocks.append("\n".join(lines))
    others = [p for p in files if not p.endswith(".py")]
    if others:
        blocks.append("### Other files\n" + "\n".join(f"- `{p}`" for p in others))
    return "\n\n".join(blocks)
