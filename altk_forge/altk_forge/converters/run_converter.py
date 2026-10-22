"""Runs a generated converter in its own process (started by converters.run, never by hand).

stdin:  {"code": "...", "items": [{"trace": {...}, "context": {...}}]}
stdout: [{"ok": true, "result": {...}} | {"ok": false, "error": "..."}], one per item

The code was checked before it got here (converters.check_code); here it also runs with a reduced set of builtins and
an import that admits only the allowed modules, so a check that missed something still cannot open files or sockets.
"""

import builtins
import importlib
import json
import sys
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
            tb = traceback.extract_tb(e.__traceback__)
            where = next((f"line {f.lineno}" for f in reversed(tb) if f.filename == "converter.py"), "")
            out.append({"ok": False, "error": f"{type(e).__name__}: {e}" + (f" ({where})" if where else "")})
    sys.stdout.write(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
