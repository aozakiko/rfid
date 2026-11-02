"""Static and runtime guards for generated RFID protocol candidates.

The project still supports historical legacy protocols whose simulator runs
tag-side logic in the same Python method as reader-side logic.  For that
reason the security checks expose an explicit ``legacy`` compatibility mode
and a ``strict`` candidate mode.  New generated candidates should use strict
mode; legacy compatibility keeps old protocol assets runnable while their
warnings remain visible in validation reports.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any


SENSITIVE_ATTRIBUTES = frozenset({"price", "value", "__dict__"})
BLOCKED_RUNTIME_ATTRIBUTES = frozenset(
    {
        "__annotations__",
        "__class__",
        "__dict__",
        "__getattribute__",
        "__getattr__",
        "__setattr__",
        "__slots__",
    }
)
BLOCKED_IMPORT_ROOTS = frozenset(
    {
        "builtins",
        "ctypes",
        "importlib",
        "multiprocessing",
        "os",
        "pathlib",
        "pty",
        "requests",
        "shlex",
        "shutil",
        "socket",
        "subprocess",
        "sys",
        "tempfile",
        "urllib",
    }
)
ALLOWED_IMPORT_ROOTS = frozenset({
    "__future__", "base", "hash", "math", "random", "time", "typing",
    "collections", "statistics", "itertools", "functools", "operator",
    "dataclasses", "enum", "heapq", "bisect", "array", "decimal", "fractions", "numpy",
})
BLOCKED_CALLS = frozenset({"__import__", "compile", "dir", "eval", "exec", "globals", "help", "input", "locals", "open", "vars"})
BLOCKED_MEMBERS = frozenset({"fromfile", "tofile", "memmap", "ctypeslib", "attrgetter", "methodcaller"})
TAG_HELPER_PREFIXES = (
    "_simulate_tag",
    "simulate_tag",
    "_tag_response",
    "tag_response",
    "_get_tag_",
    "get_tag_",
    "_safe_attr",
    "safe_attr",
    "_tag_value",
    "tag_value",
    "_bucket_bit_for_tag",
    "_price_bit_for_tag",
    "_tag_matches_",
    "tag_matches_",
)


def _module_root(name: str) -> str:
    return str(name or "").split(".", 1)[0]


def _helper_allowed(function_name: str | None) -> bool:
    """Return whether a method is explicitly marked as tag-side logic."""
    if not function_name:
        return False
    lowered = function_name.lower()
    return lowered.startswith(TAG_HELPER_PREFIXES)


class _SecurityVisitor(ast.NodeVisitor):
    def __init__(self, *, strict: bool):
        self.strict = strict
        self.function_stack: list[str] = []
        self.tag_aliases: set[str] = {"tag", "tags", "t", "item", "label"}
        self.blocked_call_aliases: set[str] = set(BLOCKED_CALLS)
        self.violations: list[dict[str, Any]] = []
        self.warnings: list[dict[str, Any]] = []

    @property
    def current_function(self) -> str | None:
        return self.function_stack[-1] if self.function_stack else None

    def _record(self, collection: list[dict[str, Any]], node: ast.AST, rule: str, message: str) -> None:
        collection.append(
            {
                "line": int(getattr(node, "lineno", 0) or 0),
                "column": int(getattr(node, "col_offset", 0) or 0),
                "rule": rule,
                "message": message,
                "function": self.current_function,
            }
        )

    def _security_issue(self, node: ast.AST, rule: str, message: str) -> None:
        if self.strict:
            self._record(self.violations, node, rule, message)
        else:
            self._record(self.warnings, node, rule, message)

    def _is_tag_expr(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Subscript):
            return self._is_tag_expr(node.value)
        if isinstance(node, ast.Attribute):
            return self._is_tag_expr(node.value)
        return isinstance(node, ast.Name) and node.id.lower() in self.tag_aliases

    @staticmethod
    def _assigned_names(node: ast.AST) -> set[str]:
        if isinstance(node, ast.Name):
            return {node.id}
        if isinstance(node, (ast.Tuple, ast.List)):
            names: set[str] = set()
            for item in node.elts:
                names.update(_SecurityVisitor._assigned_names(item))
            return names
        return set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> Any:
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()

    def visit_For(self, node: ast.For) -> Any:
        if self._is_tag_expr(node.iter):
            self.tag_aliases.update(self._assigned_names(node.target))
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> Any:
        if self._is_tag_expr(node.value):
            for target in node.targets:
                self.tag_aliases.update(self._assigned_names(target))
        if isinstance(node.value, ast.Name) and node.value.id in self.blocked_call_aliases:
            self.blocked_call_aliases.update(
                name for target in node.targets for name in self._assigned_names(target)
            )
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> Any:
        for alias in node.names:
            root = _module_root(alias.name)
            if root in BLOCKED_IMPORT_ROOTS:
                self._security_issue(node, "blocked_import", f"禁止导入模块 {alias.name}")
            elif root not in ALLOWED_IMPORT_ROOTS:
                self._security_issue(node, "import_not_allowed", f"模块不在允许列表中: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> Any:
        root = _module_root(node.module or "")
        if root in BLOCKED_IMPORT_ROOTS:
            self._security_issue(node, "blocked_import", f"禁止导入模块 {node.module}")
        elif node.level or root not in ALLOWED_IMPORT_ROOTS:
            self._security_issue(node, "import_not_allowed", f"模块不在允许列表中: {node.module}")
        for alias in node.names:
            if alias.name in BLOCKED_MEMBERS or alias.name == "*":
                self._security_issue(node, "blocked_call", f"禁止导入成员 {alias.name}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> Any:
        if isinstance(node.func, ast.Name) and node.func.id in self.blocked_call_aliases:
            self._security_issue(node, "blocked_call", f"禁止调用 {node.func.id}()")

        if isinstance(node.func, ast.Attribute):
            root = node.func.value
            if isinstance(root, ast.Name) and root.id in {
                "os",
                "subprocess",
                "socket",
                "shutil",
                "pathlib",
                "sys",
                "importlib",
            }:
                self._security_issue(node, "blocked_call", f"禁止调用 {root.id}.{node.func.attr}()")
            if node.func.attr in {"__getattribute__", "__getattr__", "__setattr__"}:
                self._security_issue(node, "blocked_introspection", f"禁止调用对象内部反射方法 {node.func.attr}()")

        if isinstance(node.func, ast.Name) and node.func.id == "getattr" and node.args:
            first = node.args[0]
            attr_name = node.args[1].value if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) else None
            if self._is_tag_expr(first) and attr_name in SENSITIVE_ATTRIBUTES and not _helper_allowed(self.current_function):
                self._security_issue(node, "direct_tag_attribute", f"读写器逻辑直接访问标签属性 {attr_name}")

        if isinstance(node.func, ast.Subscript):
            # Prevent reaching dangerous builtins through __builtins__["..."]
            # when the direct call spelling is hidden from the visitor.
            base = node.func.value
            key = node.func.slice
            key_name = key.value if isinstance(key, ast.Constant) and isinstance(key.value, str) else None
            if isinstance(base, ast.Name) and base.id == "__builtins__" and key_name in BLOCKED_CALLS:
                self._security_issue(node, "blocked_call", f"禁止通过 __builtins__ 调用 {key_name}()")

        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> Any:
        if node.attr in BLOCKED_MEMBERS:
            self._security_issue(node, "blocked_call", f"禁止访问成员 {node.attr}")
        if node.attr.startswith("__") and node.attr not in {"__init__", "__name__"}:
            self._security_issue(node, "blocked_introspection", f"禁止访问内部反射属性 {node.attr}")
        if self._is_tag_expr(node.value) and not _helper_allowed(self.current_function):
            if node.attr in SENSITIVE_ATTRIBUTES:
                self._security_issue(node, "direct_tag_attribute", f"读写器逻辑直接访问标签属性 {node.attr}")
            elif node.attr in BLOCKED_RUNTIME_ATTRIBUTES:
                self._security_issue(node, "blocked_introspection", f"禁止访问标签反射属性 {node.attr}")
        self.generic_visit(node)


def analyze_source(source: str, *, strict: bool = True, source_name: str = "<protocol>") -> dict[str, Any]:
    """Analyze protocol source and return a serializable validation report."""
    try:
        tree = ast.parse(str(source), filename=source_name)
    except SyntaxError as exc:
        issue = {
            "line": int(exc.lineno or 0),
            "column": int(exc.offset or 0),
            "rule": "syntax_error",
            "message": str(exc),
            "function": None,
        }
        return {
            "passed": False,
            "strict": strict,
            "ast_parsed": False,
            "violations": [issue],
            "warnings": [],
            "violation_count": 1,
            "warning_count": 0,
            "scope": "static_ast",
            "note": "AST 解析失败，候选不得进入执行阶段。",
        }

    visitor = _SecurityVisitor(strict=strict)
    visitor.visit(tree)
    violations = visitor.violations[:50]
    warnings = visitor.warnings[:50]
    return {
        "passed": not violations,
        "strict": strict,
        "ast_parsed": True,
        "violations": violations,
        "warnings": warnings,
        "violation_count": len(visitor.violations),
        "warning_count": len(visitor.warnings),
        "scope": "static_ast",
        "allowed_imports": sorted(ALLOWED_IMPORT_ROOTS),
        "note": (
            "软件结构检查；指定标签响应辅助方法允许仿真读取，不能证明匿名性或隐私安全。"
            if strict
            else "兼容模式保留历史协议，违规访问作为告警记录，不代表通过严格信息隔离验证。"
        ),
    }


def analyze_file(path: str | Path, *, strict: bool = True) -> dict[str, Any]:
    file_path = Path(path)
    try:
        source = file_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        return {
            "passed": False,
            "strict": strict,
            "ast_parsed": False,
            "violations": [{"line": 0, "column": 0, "rule": "source_encoding_error" if isinstance(exc, UnicodeError) else "read_error", "message": str(exc), "function": None}],
            "warnings": [],
            "violation_count": 1,
            "warning_count": 0,
            "scope": "static_ast",
            "note": "无法读取候选源码。",
        }
    report = analyze_source(source, strict=strict, source_name=str(file_path))
    report["path"] = str(file_path)
    return report


class InformationIsolationViolation(RuntimeError):
    """Raised when strict runtime mode observes a direct sensitive read."""


class TagIsolationProxy:
    """Proxy that allows IDs but blocks sensitive tag reads outside tag helpers."""

    __slots__ = ("_tag", "_protocol_file")

    def __init__(self, tag: Any, protocol_file: str | None = None):
        object.__setattr__(self, "_tag", tag)
        object.__setattr__(self, "_protocol_file", str(protocol_file or ""))

    def _inside_tag_helper(self) -> bool:
        protocol_file = object.__getattribute__(self, "_protocol_file")
        for frame_info in inspect.stack(context=0)[2:]:
            frame = frame_info.frame
            if protocol_file and str(frame.f_code.co_filename) != protocol_file:
                continue
            if _helper_allowed(frame.f_code.co_name):
                return True
        return False

    def __getattribute__(self, name: str) -> Any:
        if name in {"__repr__", "__str__", "__format__", "__dir__"}:
            return object.__getattribute__(self, name)
        if name.startswith("_"):
            raise InformationIsolationViolation(f"strict runtime isolation blocked proxy internals: {name}")
        if name in BLOCKED_RUNTIME_ATTRIBUTES:
            raise InformationIsolationViolation(f"strict runtime isolation blocked tag introspection: {name}")
        tag = object.__getattribute__(self, "_tag")
        if name in SENSITIVE_ATTRIBUTES and not object.__getattribute__(self, "_inside_tag_helper")():
            raise InformationIsolationViolation(
                f"strict runtime isolation blocked direct tag attribute access: {name}"
            )
        return getattr(tag, name)

    def __repr__(self) -> str:
        tag = object.__getattribute__(self, "_tag")
        tag_id = getattr(tag, "id", "?")
        return f"<TagIsolationProxy id={tag_id!r}>"

    def __str__(self) -> str:
        return TagIsolationProxy.__repr__(self)

    def __dir__(self) -> list[str]:
        return ["id"]


def wrap_tags(tags: Any, *, protocol_file: str | None = None, strict: bool = False) -> list[Any]:
    if not strict:
        return list(tags or [])
    return [TagIsolationProxy(tag, protocol_file=protocol_file) for tag in (tags or [])]
