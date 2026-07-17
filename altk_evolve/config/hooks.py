"""Configuration models for the memory hook seam."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


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

    ``enabled`` defaults to False, guaranteeing zero behavior change for
    existing users. When True, the execution engine — the optional ``cpex``
    package — must be installed (``pip install 'altk-evolve[hooks]'``).
    """

    enabled: bool = Field(default=False, description="Master switch. False = the hook seam is a fast no-op.")
    plugins_yaml: str | None = Field(
        default=None,
        description="Path to an engine plugins.yaml (CPEX format). Loaded by the CPEX PluginManager when set.",
    )
    plugins: list[HookPluginSpec] = Field(
        default_factory=list,
        description="Code-first plugin specs, registered in addition to any plugins_yaml entries.",
    )
    plugin_timeout: int = Field(default=30, description="Maximum execution time per plugin invocation, in seconds.")
