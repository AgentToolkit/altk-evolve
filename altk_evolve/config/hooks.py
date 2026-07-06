"""Configuration models for the memory hook seam."""

from typing import Literal

from pydantic import BaseModel, Field


class HookPluginSpec(BaseModel):
    """Code-first equivalent of one entry in a CPEX ``plugins.yaml``.

    Lets library users enable plugins programmatically without shipping a YAML
    file: each spec is synthesized into a ``cpex`` ``PluginConfig`` and the
    plugin class at ``kind`` is instantiated with it.
    """

    name: str = Field(description="Unique plugin name.")
    kind: str = Field(description="Dotted import path of the plugin class.")
    hooks: list[str] = Field(description="Hook types the plugin subscribes to (see altk_evolve.hooks.HookType).")
    mode: Literal["transform", "sequential", "concurrent", "audit", "fire_and_forget", "disabled"] = Field(
        default="transform",
        description="Execution mode. 'transform' chains payload modifications; 'sequential' may halt; 'fire_and_forget' is side-effect only.",
    )
    priority: int = Field(default=50, description="Lower runs earlier.")
    on_error: Literal["fail", "ignore", "disable"] = Field(default="ignore", description="What to do when the plugin raises.")
    config: dict = Field(default_factory=dict, description="Plugin-specific configuration, passed to the plugin constructor.")


class HooksConfig(BaseModel):
    """Hook seam configuration (``EvolveConfig.hooks``).

    ``enabled`` defaults to False, guaranteeing zero behavior change for
    existing users. When True, the optional ``cpex`` package must be installed
    (``pip install 'altk-evolve[hooks]'``).
    """

    enabled: bool = Field(default=False, description="Master switch. False = the hook seam is a fast no-op.")
    plugins_yaml: str | None = Field(
        default=None,
        description="Path to a CPEX plugins.yaml. Loaded by the CPEX PluginManager when set.",
    )
    plugins: list[HookPluginSpec] = Field(
        default_factory=list,
        description="Code-first plugin specs, registered in addition to any plugins_yaml entries.",
    )
    plugin_timeout: int = Field(default=30, description="Maximum execution time per plugin invocation, in seconds.")
