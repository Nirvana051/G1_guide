"""The central architectural rules, mechanised.

``g1_api/{models,core,state,adapters,services,tour}`` are pure stdlib -- they
must not import ``fastapi``, ``pydantic``, ``starlette`` or ``uvicorn``. Only
``gateway`` and ``console`` may.

The check walks the AST, so it is exact (an import inside a function still
counts) and does not need the forbidden packages installed.
"""

from __future__ import annotations

import ast
import os
from typing import Dict, Iterator, List, Tuple

import pytest

from tests.support.harness import PACKAGE_ROOT

FORBIDDEN_ROOTS = ("fastapi", "pydantic", "starlette", "uvicorn")
PURE_PACKAGES = ("models", "core", "state", "adapters", "services", "tour")
GATEWAY_PACKAGES = ("gateway", "console")
#: Pure *logic* layers that must never speak to a network. ``adapters`` is the
#: hardware boundary -- the real G1 hand adapter legitimately opens a TCP socket
#: to the hand server -- so it is excluded from this particular check.
NETWORK_FREE_PACKAGES = ("models", "core", "state", "services", "tour")


def _iter_python_files(root: str) -> Iterator[str]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _module_name(path: str) -> str:
    relative = os.path.relpath(path, os.path.dirname(PACKAGE_ROOT))
    trimmed = relative[: -len(".py")]
    parts = trimmed.split(os.sep)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imported_roots(tree: ast.AST) -> List[Tuple[str, int]]:
    found: List[Tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name.split(".")[0], node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            if node.module:
                found.append((node.module.split(".")[0], node.lineno))
    return found


def _pure_modules() -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for package in PURE_PACKAGES:
        root = os.path.join(PACKAGE_ROOT, package)
        assert os.path.isdir(root), "expected %s to exist" % root
        for path in _iter_python_files(root):
            out.append((_module_name(path), path))
    return sorted(out)


PURE_MODULES = _pure_modules()


def test_the_pure_packages_are_not_empty() -> None:
    assert len(PURE_MODULES) >= 20, "only %d pure modules -- the check would be vacuous" % len(PURE_MODULES)
    by_package: Dict[str, int] = {}
    for name, _ in PURE_MODULES:
        by_package[name.split(".")[1]] = by_package.get(name.split(".")[1], 0) + 1
    for package in PURE_PACKAGES:
        assert by_package.get(package, 0) > 0, "no modules walked under %s" % package


@pytest.mark.parametrize(
    "module_name,path", PURE_MODULES, ids=[name for name, _ in PURE_MODULES]
)
def test_pure_module_imports_no_web_framework(module_name: str, path: str) -> None:
    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    offences = [
        (root, lineno)
        for root, lineno in _imported_roots(tree)
        if root in FORBIDDEN_ROOTS
    ]
    assert not offences, "%s must not import a web framework, but imports %s" % (
        module_name,
        ", ".join("%s (line %d)" % (root, lineno) for root, lineno in offences),
    )


def test_adapters_do_not_import_the_core() -> None:
    offences: List[str] = []
    for name, path in PURE_MODULES:
        if not name.startswith("g1_api.adapters"):
            continue
        with open(path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom) and not node.level:
                module = node.module
            elif isinstance(node, ast.Import):
                module = ",".join(alias.name for alias in node.names)
            if module and "g1_api.core" in module:
                offences.append("%s line %d: %s" % (name, node.lineno, module))
    assert not offences, "adapters must not import g1_api.core:\n" + "\n".join(offences)


def test_models_do_not_import_services_or_core() -> None:
    allowed_internal = ("g1_api.models", "g1_api.errors", "g1_api.utils")
    offences: List[str] = []
    for name, path in PURE_MODULES:
        if not name.startswith("g1_api.models"):
            continue
        with open(path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom) and not node.level:
                module = node.module or ""
            elif isinstance(node, ast.Import):
                module = node.names[0].name
            if not module or not module.startswith("g1_api"):
                continue
            if not any(module.startswith(prefix) for prefix in allowed_internal):
                offences.append("%s line %d imports %s" % (name, node.lineno, module))
    assert not offences, "g1_api.models must not import upward:\n" + "\n".join(offences)


def test_gateway_and_console_import_a_web_framework() -> None:
    importers: List[str] = []
    for package in GATEWAY_PACKAGES:
        root = os.path.join(PACKAGE_ROOT, package)
        if not os.path.isdir(root):
            continue
        for path in _iter_python_files(root):
            with open(path, "r", encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=path)
            if any(root_name in FORBIDDEN_ROOTS for root_name, _ in _imported_roots(tree)):
                importers.append(_module_name(path))
    assert importers, "no module under gateway/console imports a web framework"


def test_the_suite_itself_cannot_open_a_network_socket() -> None:
    import socket

    from tests.support.harness import NetworkAccessAttempted

    with pytest.raises(NetworkAccessAttempted):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with pytest.raises(NetworkAccessAttempted):
        socket.create_connection(("192.168.123.164", 1448), timeout=0.1)
    left, right = socket.socketpair()
    left.close()
    right.close()


def test_pure_logic_layers_do_not_import_a_network_client() -> None:
    forbidden = ("socket", "requests", "urllib3", "aiohttp", "http", "urllib", "ftplib")
    offences: List[str] = []
    for name, path in PURE_MODULES:
        if name.split(".")[1] not in NETWORK_FREE_PACKAGES:
            continue
        with open(path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
        for root, lineno in _imported_roots(tree):
            if root in forbidden:
                offences.append("%s line %d imports %s" % (name, lineno, root))
    assert not offences, "pure logic layers must not import a network client:\n" + "\n".join(offences)
