"""The package must run on Python 3.8.10.

Three checks: byte-compilation, an AST sweep for 3.9+ constructs that parse on
3.8 but explode at runtime, and an import sweep (every module, including the
real G1 adapter whose ROS/DDS imports are guarded).
"""

from __future__ import annotations

import ast
import compileall
import io
import os
import sys
from typing import Iterator, List, Set, Tuple

import pytest

from tests.support.harness import PACKAGE_ROOT, PROJECT_ROOT

FORBIDDEN_ATTRIBUTES = {
    "functools": {"cache"},
    "asyncio": {"to_thread", "TaskGroup", "timeout", "Runner"},
    "typing": {"Self", "TypeAlias", "ParamSpec", "Concatenate", "TypeGuard", "Never"},
    "importlib.resources": {"files", "as_file"},
    "math": {"lcm", "nextafter", "ulp", "cbrt", "exp2"},
    "itertools": {"pairwise", "batched"},
}
FORBIDDEN_STR_METHODS = {"removeprefix", "removesuffix"}
BUILTIN_GENERIC_NAMES = {"list", "dict", "set", "frozenset", "tuple", "type"}
ABC_GENERIC_NAMES = {
    "Sequence", "Mapping", "MutableMapping", "Iterable", "Iterator",
    "AsyncIterator", "AsyncIterable", "Callable", "Awaitable", "Coroutine",
    "Generator", "AsyncGenerator",
}


def _iter_python_files(root: str) -> Iterator[str]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _all_sources() -> List[str]:
    files = list(_iter_python_files(PACKAGE_ROOT))
    tools = os.path.join(PROJECT_ROOT, "tools")
    if os.path.isdir(tools):
        files.extend(_iter_python_files(tools))
    return sorted(files)


ALL_SOURCES = _all_sources()
PACKAGE_SOURCES = sorted(_iter_python_files(PACKAGE_ROOT))


def _rel(path: str) -> str:
    return os.path.relpath(path, PROJECT_ROOT)


def test_running_interpreter_is_python_38() -> None:
    assert sys.version_info[:2] == (3, 8), (
        "this suite must run on 3.8; it is running on %d.%d" % sys.version_info[:2]
    )


def test_whole_package_byte_compiles() -> None:
    ok = compileall.compile_dir(PACKAGE_ROOT, quiet=1, force=True, legacy=False, optimize=0)
    assert ok, "g1_api does not byte-compile under Python %d.%d" % sys.version_info[:2]


def _annotation_nodes(tree: ast.AST) -> Iterator[Tuple[ast.AST, str]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and node.annotation is not None:
            yield node.annotation, "variable annotation"
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns is not None:
                yield node.returns, "return annotation of %s()" % node.name
            everything = list(node.args.args) + list(node.args.kwonlyargs)
            for arg in everything:
                if arg.annotation is not None:
                    yield arg.annotation, "annotation of %s(%s)" % (node.name, arg.arg)


def _find_pep604_unions(tree: ast.AST) -> List[Tuple[int, str]]:
    out: List[Tuple[int, str]] = []
    for annotation, where in _annotation_nodes(tree):
        for node in ast.walk(annotation):
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
                out.append((node.lineno, "PEP-604 union in %s" % where))
    return out


def _find_builtin_generics(tree: ast.AST) -> List[Tuple[int, str]]:
    out: List[Tuple[int, str]] = []
    for annotation, where in _annotation_nodes(tree):
        for node in ast.walk(annotation):
            if not isinstance(node, ast.Subscript):
                continue
            name = None
            if isinstance(node.value, ast.Name):
                name = node.value.id
            elif isinstance(node.value, ast.Attribute):
                name = node.value.attr
            if name in BUILTIN_GENERIC_NAMES:
                out.append((node.lineno, "builtin generic %s[...] in %s" % (name, where)))
            elif name in ABC_GENERIC_NAMES and _is_abc_import(tree, name):
                out.append((node.lineno, "collections.abc %s[...] in %s" % (name, where)))
    return out


def _is_abc_import(tree: ast.AST, name: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in ("collections.abc", "contextlib"):
            if any(alias.asname or alias.name == name for alias in node.names):
                return True
    return False


def _find_forbidden_apis(tree: ast.AST) -> List[Tuple[int, str]]:
    out: List[Tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            owner = node.value
            if isinstance(owner, ast.Name):
                banned = FORBIDDEN_ATTRIBUTES.get(owner.id, set())
                if node.attr in banned:
                    out.append((node.lineno, "%s.%s is Python 3.9+" % (owner.id, node.attr)))
            if node.attr in FORBIDDEN_STR_METHODS:
                out.append((node.lineno, "str.%s() is Python 3.9+" % node.attr))
    return out


@pytest.mark.parametrize("path", ALL_SOURCES, ids=[_rel(p) for p in ALL_SOURCES])
def test_source_uses_only_python38_constructs(path: str) -> None:
    with open(path, "r", encoding="utf-8") as handle:
        source = handle.read()
    tree = ast.parse(source, filename=path)
    problems: List[Tuple[int, str]] = []
    problems.extend(_find_pep604_unions(tree))
    problems.extend(_find_builtin_generics(tree))
    problems.extend(_find_forbidden_apis(tree))
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("match ") and stripped.endswith(":"):
            problems.append((0, "match/case is Python 3.10+"))
    assert not problems, "%s uses constructs unavailable on Python 3.8:\n%s" % (
        _rel(path),
        "\n".join("  line %d: %s" % item for item in sorted(problems)),
    )


def test_every_package_module_imports_cleanly() -> None:
    import importlib

    failures: List[str] = []
    for path in PACKAGE_SOURCES:
        relative = os.path.relpath(path, os.path.dirname(PACKAGE_ROOT))
        parts = relative[: -len(".py")].split(os.sep)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        name = ".".join(parts)
        if not name:
            continue
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001
            failures.append("%s: %s: %s" % (name, type(exc).__name__, exc))
    assert not failures, "modules that fail to import:\n" + "\n".join(failures)
