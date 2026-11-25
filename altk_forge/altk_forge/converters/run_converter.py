"""Runs a generated converter in its own process (started by converters.run, never by hand).

stdin:  {"code": "...", "items": [{"trace": {...}, "context": {...}}]}
stdout: [{"ok": true, "result": {...}} | {"ok": false, "error": "..."}], one per item

The code was checked before it got here (converters.check_code); here it also runs with a reduced set of builtins and
an import that admits only the allowed modules, so a check that missed something still cannot open files or sockets.
"""

import builtins
import importlib
import io
import json
import keyword
import sys
import tokenize
import traceback

ALLOWED = {
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
    "urllib",
    "jsonschema",
}
BLOCKED = {
    "open",
    "exec",
    "eval",
    "compile",
    "input",
    "breakpoint",
    "exit",
    "quit",
    "help",
    "globals",
    "locals",
    "vars",
    "memoryview",
    "setattr",
    "delattr",
}
for name in ALLOWED - {"urllib"}:
    importlib.import_module(name)


def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level or name not in ALLOWED:
        raise ImportError(f"import {name} is not allowed in a converter")
    return importlib.__import__(name, globals, locals, fromlist, level)


# How much of the failing line's variables the error shows: enough to see a value's type and shape, short enough that
# the error stays readable when a variable holds the whole trace.
SHOWN_VALUE = 160
SHOWN_NAMES = 8


def describe_failure(e, code):
    """`Type: message`, then where in converter.py it happened: the line itself, and the type and start of every
    variable that line uses. A model fixing its code needs to see *which* value had the wrong shape ('output' was a
    str), not only a line number."""
    head = f"{type(e).__name__}: {e}"
    frames = [f for f in traceback.walk_tb(e.__traceback__) if f[0].f_code.co_filename == "converter.py"]
    if not frames:
        return head
    frame, lineno = frames[-1]
    lines = code.splitlines()
    source = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
    used = [n for n in _read_names(source) if n in frame.f_locals]
    shown = []
    for name in used[:SHOWN_NAMES]:
        value = frame.f_locals[name]
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = repr(value)
        if len(text) > SHOWN_VALUE:
            text = text[:SHOWN_VALUE] + "…"
        shown.append(f"{name} = {text} ({type(value).__name__})")
    out = f"{head} at line {lineno}: {source}"
    return out + ("; where " + "; ".join(shown) if shown else "")


def _read_names(source):
    """The names a line reads, in order: not those it assigns (left of `=`), which still hold an earlier value when
    the line fails, nor attribute names (the `get` of `x.get`)."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError):
        return []
    assign = next((i for i, tok in enumerate(tokens) if tok.type == tokenize.OP and tok.string in ("=", "+=", "-=", "|=")), -1)
    out = []
    for i, tok in enumerate(tokens):
        after_dot = i and tokens[i - 1].type == tokenize.OP and tokens[i - 1].string == "."
        if tok.type == tokenize.NAME and i > assign and not after_dot and not keyword.iskeyword(tok.string) and tok.string not in out:
            out.append(tok.string)
    return out


def main():
    job = json.loads(sys.stdin.read())
    safe = {k: v for k, v in vars(builtins).items() if k not in BLOCKED}
    safe["__import__"] = guarded_import
    real_getattr, real_hasattr = getattr, hasattr

    def public(name):
        if not isinstance(name, str) or name.startswith("_"):
            raise AttributeError(f"attribute {name!r} is not allowed in a converter")
        return name

    safe["getattr"] = lambda obj, name, *default: real_getattr(obj, public(name), *default)
    safe["hasattr"] = lambda obj, name: real_hasattr(obj, public(name))
    env = {"__builtins__": safe, "__name__": "converter"}
    out = []
    try:
        exec(compile(job["code"], "converter.py", "exec"), env)  # noqa: S102 - the point of this process
        convert = env["convert"]
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        print(json.dumps([{"ok": False, "error": f"loading the converter: {err}"} for _ in job["items"]]))
        return
    for item in job["items"]:
        try:
            result = convert(item["trace"], item.get("context") or {})
            out.append({"ok": True, "result": json.loads(json.dumps(result, ensure_ascii=False, default=str))})
        except Exception as e:
            out.append({"ok": False, "error": describe_failure(e, job["code"])})
    sys.stdout.write(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
