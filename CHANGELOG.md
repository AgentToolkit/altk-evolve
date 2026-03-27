# CHANGELOG

<!-- version list -->

## v1.0.5 (2026-03-12)


## v1.0.4 (2026-03-12)


## v1.0.3 (2026-03-12)

### Bug Fixes

- **save-trajectory**: Address code review findings
  ([#89](https://github.com/AgentToolkit/altk-evolve/pull/89),
  [`6e6438b`](https://github.com/AgentToolkit/altk-evolve/commit/6e6438b285f562d15c9dc191b96a47a04d7d4e73))

- **save-trajectory**: Make log() best-effort so debug logging never crashes the script
  ([#89](https://github.com/AgentToolkit/altk-evolve/pull/89),
  [`6e6438b`](https://github.com/AgentToolkit/altk-evolve/commit/6e6438b285f562d15c9dc191b96a47a04d7d4e73))

- **save-trajectory**: Restrict file permissions on trajectories dir and output files
  ([#89](https://github.com/AgentToolkit/altk-evolve/pull/89),
  [`6e6438b`](https://github.com/AgentToolkit/altk-evolve/commit/6e6438b285f562d15c9dc191b96a47a04d7d4e73))

- **save-trajectory**: Use EXIT trap for temp file cleanup to handle script failures
  ([#89](https://github.com/AgentToolkit/altk-evolve/pull/89),
  [`6e6438b`](https://github.com/AgentToolkit/altk-evolve/commit/6e6438b285f562d15c9dc191b96a47a04d7d4e73))

- **save-trajectory**: Validate trajectory type and broaden input error handling
  ([#89](https://github.com/AgentToolkit/altk-evolve/pull/89),
  [`6e6438b`](https://github.com/AgentToolkit/altk-evolve/commit/6e6438b285f562d15c9dc191b96a47a04d7d4e73))

### Documentation

- **save-trajectory**: Add skill to plugin README
  ([#89](https://github.com/AgentToolkit/altk-evolve/pull/89),
  [`6e6438b`](https://github.com/AgentToolkit/altk-evolve/commit/6e6438b285f562d15c9dc191b96a47a04d7d4e73))

- **save-trajectory**: Update module docstring to reflect file path argument
  ([#89](https://github.com/AgentToolkit/altk-evolve/pull/89),
  [`6e6438b`](https://github.com/AgentToolkit/altk-evolve/commit/6e6438b285f562d15c9dc191b96a47a04d7d4e73))

### Features

- Add PostgreSQL/pgvector backend ([#86](https://github.com/AgentToolkit/altk-evolve/pull/86),
  [`50efcd2`](https://github.com/AgentToolkit/altk-evolve/commit/50efcd2161e9c98ad127e8494bf6bf8f0b726067))

- Add save-trajectory skill to export conversation as OpenAI chat format
  ([#89](https://github.com/AgentToolkit/altk-evolve/pull/89),
  [`6e6438b`](https://github.com/AgentToolkit/altk-evolve/commit/6e6438b285f562d15c9dc191b96a47a04d7d4e73))

- **backend**: Refactor entity update with template method pattern and upgrade dependencies
  ([#86](https://github.com/AgentToolkit/altk-evolve/pull/86),
  [`50efcd2`](https://github.com/AgentToolkit/altk-evolve/commit/50efcd2161e9c98ad127e8494bf6bf8f0b726067))


## v1.0.2 (2026-03-05)

### Bug Fixes

- Include jinja prompt templates in package artifacts
  ([#85](https://github.com/AgentToolkit/altk-evolve/pull/85),
  [`0c29aba`](https://github.com/AgentToolkit/altk-evolve/commit/0c29abadae3b0f537761e42a32d6812a7efbe2c6))


## v1.0.1 (2026-03-04)

### Bug Fixes

- **packaging**: Include evolve subpackages in distribution
  ([#84](https://github.com/AgentToolkit/altk-evolve/pull/84),
  [`1bac14c`](https://github.com/AgentToolkit/altk-evolve/commit/1bac14cdb08b85c46dd87b36368431c802367d1e))


## v1.0.0 (2026-03-04)

### Bug Fixes

- Add Pydantic validation error handling for LLM tip generation and validate trajectory data input.
  ([#56](https://github.com/AgentToolkit/altk-evolve/pull/56),
  [`d2a4d0a`](https://github.com/AgentToolkit/altk-evolve/commit/d2a4d0a8e107d1a6475c31b052c9afe77e5b4784))

- Address CodeRabbit review feedback (round 3)
  ([#60](https://github.com/AgentToolkit/altk-evolve/pull/60),
  [`8ea2051`](https://github.com/AgentToolkit/altk-evolve/commit/8ea20516b0509bd7b9e8a5c02d6943dcf0e58bd5))

- Address CodeRabbit review feedback (round 4)
  ([#60](https://github.com/AgentToolkit/altk-evolve/pull/60),
  [`8ea2051`](https://github.com/AgentToolkit/altk-evolve/commit/8ea20516b0509bd7b9e8a5c02d6943dcf0e58bd5))

- Address CodeRabbit review feedback on tip clustering
  ([#60](https://github.com/AgentToolkit/altk-evolve/pull/60),
  [`8ea2051`](https://github.com/AgentToolkit/altk-evolve/commit/8ea20516b0509bd7b9e8a5c02d6943dcf0e58bd5))

- Clean up stale variable name and deduplicate entity file locations
  ([#67](https://github.com/AgentToolkit/altk-evolve/pull/67),
  [`9dcdc54`](https://github.com/AgentToolkit/altk-evolve/commit/9dcdc5412a81941d9f580f371179f776f55fa9ae))

- Correct typo in EXIF example run instructions
  ([#80](https://github.com/AgentToolkit/altk-evolve/pull/80),
  [`0b3a8e8`](https://github.com/AgentToolkit/altk-evolve/commit/0b3a8e827c4f2a088389d901fb50f9542d24eae1))

- Enhance LLM tip generation robustness ([#56](https://github.com/AgentToolkit/altk-evolve/pull/56),
  [`d2a4d0a`](https://github.com/AgentToolkit/altk-evolve/commit/d2a4d0a8e107d1a6475c31b052c9afe77e5b4784))

- Enhance LLM tip generation robustness by skipping empty assistant messages and handling
  malformed/empty responses, and add validation for trajectory data.
  ([#56](https://github.com/AgentToolkit/altk-evolve/pull/56),
  [`d2a4d0a`](https://github.com/AgentToolkit/altk-evolve/commit/d2a4d0a8e107d1a6475c31b052c9afe77e5b4784))

- Guard against empty tips list in MCP save_trajectory
  ([#60](https://github.com/AgentToolkit/altk-evolve/pull/60),
  [`8ea2051`](https://github.com/AgentToolkit/altk-evolve/commit/8ea20516b0509bd7b9e8a5c02d6943dcf0e58bd5))

- Guard against empty tips list in MCP save_trajectory
  ([#58](https://github.com/AgentToolkit/altk-evolve/pull/58),
  [`ce1ead3`](https://github.com/AgentToolkit/altk-evolve/commit/ce1ead30664f5c92c93a72f604bae75e6b80a2b7))

- Harden milvus filters and fact extraction input handling
  ([`36dee90`](https://github.com/AgentToolkit/altk-evolve/commit/36dee9013a04d072df577380089c9cfd0f750f92))

- Narrow created_at type handling for mypy
  ([`36dee90`](https://github.com/AgentToolkit/altk-evolve/commit/36dee9013a04d072df577380089c9cfd0f750f92))

- Prevent shell injection in sandbox-prompt via env variable
  ([#80](https://github.com/AgentToolkit/altk-evolve/pull/80),
  [`0b3a8e8`](https://github.com/AgentToolkit/altk-evolve/commit/0b3a8e827c4f2a088389d901fb50f9542d24eae1))

- Resolve filesystem delete bug and update MCP namespace logic
  ([#68](https://github.com/AgentToolkit/altk-evolve/pull/68),
  [`16aeb78`](https://github.com/AgentToolkit/altk-evolve/commit/16aeb78cc943f6794e2d209cecad94f96a1f49eb))

- Run sandbox container as non-root user and harden installer
  ([#75](https://github.com/AgentToolkit/altk-evolve/pull/75),
  [`647aea0`](https://github.com/AgentToolkit/altk-evolve/commit/647aea01e94fed91cba3706d11cb9ab21df77096))

### Documentation

- Add Roo Code custom mode integration guide ([#70](https://github.com/AgentToolkit/altk-evolve/pull/70),
  [`aa34fc4`](https://github.com/AgentToolkit/altk-evolve/commit/aa34fc412781b5ea0ae676bc67eff4c1bfbddb65))

- Add tie-breaker rule for entity count cap in learn skill
  ([#69](https://github.com/AgentToolkit/altk-evolve/pull/69),
  [`9721a05`](https://github.com/AgentToolkit/altk-evolve/commit/9721a05ccf4dfd19dd2a5edab2b624b3c0a72246))

- Fix sandbox README paths to work from repo root
  ([#75](https://github.com/AgentToolkit/altk-evolve/pull/75),
  [`647aea0`](https://github.com/AgentToolkit/altk-evolve/commit/647aea01e94fed91cba3706d11cb9ab21df77096))

- Move LiteLLM proxy details to configuration guide
  ([`36dee90`](https://github.com/AgentToolkit/altk-evolve/commit/36dee9013a04d072df577380089c9cfd0f750f92))

- **agents**: Add conventional commits format guidance for python-semantic-release
  ([#82](https://github.com/AgentToolkit/altk-evolve/pull/82),
  [`2c715dc`](https://github.com/AgentToolkit/altk-evolve/commit/2c715dc4d98e53d846e20b6c3bad0eca8250e436))

### Features

- Add error-prevention focus to learn skill and skip known-failed approaches in recovery
  ([#69](https://github.com/AgentToolkit/altk-evolve/pull/69),
  [`9721a05`](https://github.com/AgentToolkit/altk-evolve/commit/9721a05ccf4dfd19dd2a5edab2b624b3c0a72246))

- Add policy support and restore mcp backward compatibility
  ([#68](https://github.com/AgentToolkit/altk-evolve/pull/68),
  [`16aeb78`](https://github.com/AgentToolkit/altk-evolve/commit/16aeb78cc943f6794e2d209cecad94f96a1f49eb))

- Add sandbox demo tooling and EXIF extraction example
  ([#80](https://github.com/AgentToolkit/altk-evolve/pull/80),
  [`0b3a8e8`](https://github.com/AgentToolkit/altk-evolve/commit/0b3a8e827c4f2a088389d901fb50f9542d24eae1))

- Add sandbox for running Claude Code in Docker
  ([#75](https://github.com/AgentToolkit/altk-evolve/pull/75),
  [`647aea0`](https://github.com/AgentToolkit/altk-evolve/commit/647aea01e94fed91cba3706d11cb9ab21df77096))

- Add tip provenance tracking and metadata ([#73](https://github.com/AgentToolkit/altk-evolve/pull/73),
  [`1154b4a`](https://github.com/AgentToolkit/altk-evolve/commit/1154b4a4cded47264abeaa2cfd4b9a96c56edee9))

- Cluster tips by task description cosine similarity
  ([#60](https://github.com/AgentToolkit/altk-evolve/pull/60),
  [`8ea2051`](https://github.com/AgentToolkit/altk-evolve/commit/8ea20516b0509bd7b9e8a5c02d6943dcf0e58bd5))

- Cluster tips by task description similarity
  ([#60](https://github.com/AgentToolkit/altk-evolve/pull/60),
  [`8ea2051`](https://github.com/AgentToolkit/altk-evolve/commit/8ea20516b0509bd7b9e8a5c02d6943dcf0e58bd5))

- Combine tips within clusters via LLM consolidation
  ([#60](https://github.com/AgentToolkit/altk-evolve/pull/60),
  [`8ea2051`](https://github.com/AgentToolkit/altk-evolve/commit/8ea20516b0509bd7b9e8a5c02d6943dcf0e58bd5))

- Move entity storage to .evolve/ and add Evolve Lite guide
  ([#67](https://github.com/AgentToolkit/altk-evolve/pull/67),
  [`9dcdc54`](https://github.com/AgentToolkit/altk-evolve/commit/9dcdc5412a81941d9f580f371179f776f55fa9ae))

- Persist task description in tip entity metadata
  ([#60](https://github.com/AgentToolkit/altk-evolve/pull/60),
  [`8ea2051`](https://github.com/AgentToolkit/altk-evolve/commit/8ea20516b0509bd7b9e8a5c02d6943dcf0e58bd5))

- Persist task description in tip entity metadata
  ([#58](https://github.com/AgentToolkit/altk-evolve/pull/58),
  [`ce1ead3`](https://github.com/AgentToolkit/altk-evolve/commit/ce1ead30664f5c92c93a72f604bae75e6b80a2b7))

- **config**: Support LiteLLM proxy env mapping and document model precedence
  ([`36dee90`](https://github.com/AgentToolkit/altk-evolve/commit/36dee9013a04d072df577380089c9cfd0f750f92))

### Testing

- **llm**: Improve conflict resolution prompt clarity and add comprehensive test suite
  ([#82](https://github.com/AgentToolkit/altk-evolve/pull/82),
  [`2c715dc`](https://github.com/AgentToolkit/altk-evolve/commit/2c715dc4d98e53d846e20b6c3bad0eca8250e436))


## v0.2.1 (2026-02-09)


## v0.2.0 (2026-02-09)


## v0.1.0-rc.4 (2026-02-09)


## v0.1.0-rc.3 (2026-02-09)


## v0.1.0-rc.2 (2026-02-09)


## v0.1.0-rc.1 (2026-02-09)

- Initial Release
