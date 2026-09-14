"""Import helpers shared by the Hermes bundle tests.

The Hermes provider is not a package this repo installs — it is a rendered
bundle under ``platform-integrations/hermes/`` that Hermes copies into
``$HERMES_HOME/plugins/evolve/`` and imports itself. To test what actually
ships, these tests import the rendered files directly, which needs a bit of
``importlib`` care (see ``load_module``).

Underscore-prefixed so pytest does not collect it as a test module.
"""

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
HERMES_PLUGIN_ROOT = REPO_ROOT / "platform-integrations/hermes/plugins/evolve"
HOST_STUBS = Path(__file__).parent / "_hermes_host_stubs"


def is_stdlib_import(line):
    """True if an ``import x`` / ``from x import y`` line names only stdlib roots.

    Used to classify the bundle's module-level imports: anything that is neither
    stdlib nor a relative import is a Hermes host dependency.
    """
    if line.startswith("from "):
        roots = [line.split()[1]]
    else:
        roots = line[len("import ") :].split("#")[0].split(",")
    return all(r.strip().split(".")[0] in sys.stdlib_module_names for r in roots)


def load_module(name, path, extra_syspath=()):
    """Import a file from the rendered bundle under an arbitrary module name.

    ``submodule_search_locations`` plus the ``sys.modules`` registration are what
    make this work for ``__init__.py``, which uses relative imports: without the
    search locations the module is not a package and ``from .backend import ...``
    raises ``ModuleNotFoundError``, and without the registration the relative
    import cannot resolve its own parent. Registering it also means submodules
    can later be reached with ``importlib.import_module(f"{name}.backend")``.

    ``extra_syspath`` entries (the host stubs) are prepended for the duration of
    the import only, then removed — a permanent insert would leak stub packages
    into every later test in the session.
    """
    added = [str(p) for p in extra_syspath if str(p) not in sys.path]
    sys.path[:0] = added
    spec = importlib.util.spec_from_file_location(name, path, submodule_search_locations=[str(Path(path).parent)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    finally:
        for entry in added:
            if entry in sys.path:
                sys.path.remove(entry)
    return module
