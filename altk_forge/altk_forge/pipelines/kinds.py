"""What each kind of integration needs, and the choices a pipeline offers (memory settings, modes)."""

from ..memory.evolve import GUIDELINE_FIELDS
from ..sources.filters import COLUMNS

# What each kind of integration needs.
KINDS = {
    "langfuse": {
        "label": "Langfuse",
        "direction": "input",
        "fields": ["host", "public_key", "secret_key"],
        "secret": ["secret_key"],
        "import": True,
        "filter_kind": "langfuse",
        "default_host": "http://localhost:3000",
    },
    "filesystem": {
        "label": "Filesystem",
        "direction": "input",
        "fields": ["path", "recursive", "exclude"],
        "secret": [],
        "optional": ["recursive", "exclude"],
        "bool": ["recursive"],
        "import": True,
        "filter_kind": None,
        "watermark": False,
        "note": "Every *.json file in the folder is one trace and becomes one trajectory; exclude the "
        "files that are no trace (a run's summary, metadata). The folder is read on the machine "
        "the server runs on. A file is converted once: later changes to it are not picked up.",
    },
    "phoenix": {
        "label": "Arize Phoenix",
        "direction": "input",
        "fields": ["host", "api_key", "project"],
        "secret": ["api_key"],
        "optional": ["api_key", "project"],
        "import": False,
        "default_host": "http://localhost:6006",
        "note": "Connection test only: there is no Phoenix-to-trajectory converter yet.",
    },
    "altk_evolve": {
        "label": "altk-evolve",
        "direction": "output",
        "default_host": "http://127.0.0.1:8000",
        "fields": ["host", "api_key"],
        "secret": ["api_key"],
        "optional": ["api_key"],
        "note": "Only the connection. How trajectories are stored (namespaces) and how guidelines are "
        "generated from them is set on each pipeline that writes here.",
    },
}


# How a pipeline stores its trajectories in an altk-evolve memory: pipeline["memory"]. All optional.
MEMORY_FIELDS = ["namespace_mode", "namespace_prefix", "namespace_id", "processing_profile", "profile_revision", *GUIDELINE_FIELDS]


MEMORY_CHOICES = {
    "namespace_mode": ["", "per_step", "single"],
    "guidelines_mode": ["", "standard", "consistency", "all"],
    "consistency_method": ["", "fast", "accurate"],
    "segmentation_enabled": ["", "true", "false"],
}


FILTER_KINDS = {"langfuse": {"label": "Langfuse", "columns": COLUMNS}}


MODES = ("test", "production")
