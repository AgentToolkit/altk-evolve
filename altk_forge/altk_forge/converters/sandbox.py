"""Running generated code: a static check, then a separate process with a time limit (run_converter.py).

Generated code may import only a short list of standard modules and may not touch files, processes or the network.
The check here rejects what it can see; run_converter.py runs the code with reduced builtins and a guarded import,
so a check that missed something still cannot open files or sockets."""

import ast
import json
import subprocess
import sys

from ..core.paths import PACKAGE

RUNNER = PACKAGE / "converters" / "run_converter.py"
RUN_TIMEOUT = 120  # seconds for one run of a converter (all its inputs)


ALLOWED_IMPORTS = {
    "json",
    "re",
    "math",
    "datetime",
    "itertools",
    "collections",
    "functools",
    "copy",
    "hashlib",
    "string",
    "typing",
    "statistics",
    "textwrap",
    "html",
    "unicodedata",
    "urllib.parse",
    "jsonschema",
}


FORBIDDEN_NAMES = {
    "open",
    "exec",
    "eval",
    "compile",
    "__import__",
    "input",
    "globals",
    "locals",
    "vars",
    "breakpoint",
    "exit",
    "quit",
    "help",
    "memoryview",
    "setattr",
    "delattr",
}


class _Check(ast.NodeVisitor):
    def __init__(self):
        self.problems = []

    def visit_Import(self, node):
        for a in node.names:
            if a.name not in ALLOWED_IMPORTS:
                self.problems.append(f"line {node.lineno}: import {a.name} is not allowed")

    def visit_ImportFrom(self, node):
        if node.level or node.module not in ALLOWED_IMPORTS:
            self.problems.append(f"line {node.lineno}: from {node.module} import is not allowed")

    def visit_Name(self, node):
        if node.id in FORBIDDEN_NAMES:
            self.problems.append(f"line {node.lineno}: {node.id} is not allowed")

    def visit_Call(self, node):
        # getattr/hasattr are fine for plain names; a computed or private name could reach __class__ and the like.
        if isinstance(node.func, ast.Name) and node.func.id in ("getattr", "hasattr") and len(node.args) >= 2:
            name = node.args[1]
            if not (isinstance(name, ast.Constant) and isinstance(name.value, str) and not name.value.startswith("_")):
                self.problems.append(f"line {node.lineno}: {node.func.id}() takes a constant public attribute name here")
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if node.attr.startswith("__") and node.attr not in ("__name__",):
            self.problems.append(f"line {node.lineno}: attribute {node.attr} is not allowed")
        self.generic_visit(node)


def check_code(code):
    """Problems that keep generated code from running: syntax, a missing convert(), imports and names outside the
    allowed set."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"syntax error, line {e.lineno}: {e.msg}"]
    c = _Check()
    c.visit(tree)
    if not any(isinstance(n, ast.FunctionDef) and n.name == "convert" for n in tree.body):
        c.problems.append("no top-level function convert(trace, context)")
    return c.problems


def run(code, items):
    """Run convert() of `code` on [{trace, context}] in a separate process; returns [{ok, result} | {ok: False, error}]."""
    problems = check_code(code)
    if problems:
        return [{"ok": False, "error": "; ".join(problems)} for _ in items]
    payload = json.dumps({"code": code, "items": items}, ensure_ascii=False)
    try:
        p = subprocess.run(
            [sys.executable, "-I", str(RUNNER)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT,
            cwd=str(RUNNER.parent),
            env={"PYTHONHASHSEED": "0", "PATH": ""},
        )
    except subprocess.TimeoutExpired:
        return [{"ok": False, "error": f"timed out after {RUN_TIMEOUT} s"} for _ in items]
    try:
        return json.loads(p.stdout)
    except ValueError:
        err = (p.stderr or p.stdout or "no output").strip().splitlines()[-1:] or ["no output"]
        return [{"ok": False, "error": f"the converter process failed: {err[0][:300]}"} for _ in items]
