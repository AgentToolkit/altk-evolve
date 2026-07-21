"""Configuration models for the memory hook seam."""

from __future__ import annotations

import os
import warnings
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

#: Basename of the project-local hooks config auto-discovered from the cwd.
DEFAULT_HOOKS_CONFIG_FILENAME = "evolve.hooks.yaml"
#: Environment variable that, when set, points at an explicit hooks config path.
HOOKS_CONFIG_ENV_VAR = "EVOLVE_HOOKS_CONFIG"


def discover_hooks_config_path(
    *,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    user_config_dir: Path | None = None,
) -> str | None:
    """Locate a default hooks config file, searching (first hit wins):

    1. ``$EVOLVE_HOOKS_CONFIG`` — an explicit path (an env override always wins).
    2. ``./evolve.hooks.yaml`` — project-local, relative to ``cwd``.
    3. ``<user_config_dir>/evolve/hooks.yaml`` — a per-user config, where
       ``user_config_dir`` defaults to ``$XDG_CONFIG_HOME`` or ``~/.config``.

    Returns the first existing path as a string, or ``None`` when nothing is
    found (the seam then stays a zero-cost no-op). Every input is injectable so
    tests can exercise discovery without touching the real home directory.

    Note on the env var: an explicit path set via ``$EVOLVE_HOOKS_CONFIG`` is
    returned even if the file does not exist, so a typo surfaces as a clear
    "file not found" at engine init rather than silently falling through to a
    lower-priority location.
    """
    env = os.environ if env is None else env
    cwd = Path.cwd() if cwd is None else cwd

    explicit = env.get(HOOKS_CONFIG_ENV_VAR)
    if explicit:
        # Explicit path wins unconditionally — do not fall through on a typo.
        return explicit

    project_local = cwd / DEFAULT_HOOKS_CONFIG_FILENAME
    if project_local.is_file():
        return str(project_local)

    if user_config_dir is None:
        xdg = env.get("XDG_CONFIG_HOME")
        user_config_dir = Path(xdg) if xdg else Path.home() / ".config"
    user_config = user_config_dir / "evolve" / "hooks.yaml"
    if user_config.is_file():
        return str(user_config)

    return None


class HookPluginSpec(BaseModel):
    """Code-first spec for one hook plugin (equivalent of one entry in the
    execution engine's ``plugins.yaml``).

    Lets library users enable plugins programmatically without shipping a YAML
    file: each spec is synthesized into a ``PluginConfig`` for the shipped
    CPEX engine and the plugin class at ``kind`` is instantiated with it.
    """

    name: str = Field(description="Unique plugin name.")
    kind: str = Field(description="Dotted import path of the plugin class.")
    hooks: list[str] = Field(description="Hook types the plugin subscribes to (see altk_evolve.hooks.HookType).")
    mode: Literal["transform", "sequential", "concurrent", "audit", "fire_and_forget", "disabled"] = Field(
        default="transform",
        description="Execution mode. 'transform' chains payload modifications; 'sequential' may halt; 'fire_and_forget' is side-effect only.",
    )
    priority: int = Field(default=50, description="Lower runs earlier.")
    # Fail-closed by default: a compliance plugin (e.g. PII redaction) that
    # crashes or times out must halt the operation, not silently pass data
    # through. Non-critical plugins can opt into "ignore" per spec.
    on_error: Literal["fail", "ignore", "disable"] = Field(default="fail", description="What to do when the plugin raises.")
    config: dict = Field(default_factory=dict, description="Plugin-specific configuration, passed to the plugin constructor.")

    @field_validator("kind")
    @classmethod
    def _kind_is_dotted_path(cls, value: str) -> str:
        """``kind`` is imported as ``module.rpartition('.') -> (module, Class)``.

        A bare name (no ``.``) yields an empty module path and a confusing
        ImportError deep inside ``_register_spec``, so reject it up front with a
        clear message.
        """
        module_path, dot, class_name = value.rpartition(".")
        if not dot or not module_path or not class_name:
            raise ValueError(f"kind must be a dotted 'module.Class' import path, got {value!r}")
        return value


class HooksConfig(BaseModel):
    """Hook seam configuration (``EvolveConfig.hooks``).

    The hook seam is **always live** — there is no master switch. Behavior is
    determined entirely by which plugins are configured:

    - **No plugins** (empty ``plugins_yaml`` + empty code-first ``plugins`` +
      nothing auto-discovered) → the seam is a zero-cost no-op that requires no
      execution engine; importing a backend pulls no ``cpex``.
    - **Plugins configured but the engine is missing** → engine initialization
      fails **closed** with a clear error (``pip install 'altk-evolve[hooks]'``),
      never a silent no-op.

    When ``plugins_yaml`` is not set explicitly, a default config file is
    auto-discovered via :func:`discover_hooks_config_path` (``$EVOLVE_HOOKS_CONFIG``
    → ``./evolve.hooks.yaml`` → ``~/.config/evolve/hooks.yaml``). Scaffold one
    with ``evolve hooks init``. An explicit ``plugins_yaml`` (or code-first
    ``plugins``) always overrides discovery.
    """

    plugins_yaml: str | None = Field(
        default=None,
        description="Path to an engine plugins.yaml (CPEX format). Loaded by the CPEX PluginManager when set.",
    )
    plugins: list[HookPluginSpec] = Field(
        default_factory=list,
        description="Code-first plugin specs, registered in addition to any plugins_yaml entries.",
    )
    plugin_timeout: int = Field(default=30, description="Maximum execution time per plugin invocation, in seconds.")

    @model_validator(mode="before")
    @classmethod
    def _drop_deprecated_enabled(cls, data: Any) -> Any:
        """Accept and ignore the removed ``enabled`` master switch.

        Hooks are always live now; behavior is decided purely by which plugins
        are configured. A caller (or persisted config) that still passes
        ``enabled=`` must not hard-crash, so we pop the field and emit a
        ``DeprecationWarning``. It has NO effect on behavior — a stale
        ``enabled=False`` no longer disables a configured plugin.
        """
        if isinstance(data, Mapping) and "enabled" in data:
            data = dict(data)
            data.pop("enabled")
            warnings.warn(
                "HooksConfig.enabled is deprecated and ignored: the hook seam is always live. "
                "Behavior is determined solely by which plugins you configure (none = zero-cost no-op). "
                "Remove 'enabled' from your HooksConfig.",
                DeprecationWarning,
                stacklevel=2,
            )
        return data
