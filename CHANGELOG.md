# CHANGELOG

<!-- version list -->

## v1.0.10 (2026-04-20)

### Bug Fixes

- **mcp**: Align metadata filters and harden SSE teardown
  ([`a0bcc6d`](https://github.com/AgentToolkit/altk-evolve/commit/a0bcc6db5ac5fdb4808d6e11e451eb3156ba9596))

- **postgres**: Prevent ambiguous filter behavior across backends
  ([`a0bcc6d`](https://github.com/AgentToolkit/altk-evolve/commit/a0bcc6db5ac5fdb4808d6e11e451eb3156ba9596))


## v1.0.9 (2026-04-17)

### Bug Fixes

- Publish install.sh as a versioned release artifact
  ([#195](https://github.com/AgentToolkit/altk-evolve/pull/195),
  [`0b055da`](https://github.com/AgentToolkit/altk-evolve/commit/0b055da765c03fa51348defb7630643f4a48c0f1))

### Features

- **bob**: Add save-trajectory skill to Bob evolve-lite
  ([#184](https://github.com/AgentToolkit/altk-evolve/pull/184),
  [`9ca94e5`](https://github.com/AgentToolkit/altk-evolve/commit/9ca94e5ced9d0ebca03552703e3a7fe2417aae5a))


## v1.0.8 (2026-04-09)


## v1.0.6 (2026-04-03)

### Bug Fixes

- Add optional implementation_steps to Tip model and prompt
  ([#124](https://github.com/AgentToolkit/altk-evolve/pull/124),
  [`d373e7e`](https://github.com/AgentToolkit/altk-evolve/commit/d373e7ebb00ca0b3438aa1017961fbeb9cb5d0d8))

- Clarify task status context in tip generation prompt
  ([#124](https://github.com/AgentToolkit/altk-evolve/pull/124),
  [`d373e7e`](https://github.com/AgentToolkit/altk-evolve/commit/d373e7ebb00ca0b3438aa1017961fbeb9cb5d0d8))

- Completely remove all Roo references from install.sh
  ([#130](https://github.com/AgentToolkit/altk-evolve/pull/130),
  [`8e6c76d`](https://github.com/AgentToolkit/altk-evolve/commit/8e6c76d13c111da28f486a1c3d3b716f6c39ccdc))

- Normalize implementation_steps to list[str] in combine_cluster
  ([#124](https://github.com/AgentToolkit/altk-evolve/pull/124),
  [`d373e7e`](https://github.com/AgentToolkit/altk-evolve/commit/d373e7ebb00ca0b3438aa1017961fbeb9cb5d0d8))

- Remove Roo integration tests and fix install.sh 'all' platform
  ([#130](https://github.com/AgentToolkit/altk-evolve/pull/130),
  [`8e6c76d`](https://github.com/AgentToolkit/altk-evolve/commit/8e6c76d13c111da28f486a1c3d3b716f6c39ccdc))

- Resolve CI failures for formatting and type-checking
  ([#91](https://github.com/AgentToolkit/altk-evolve/pull/91),
  [`29dba09`](https://github.com/AgentToolkit/altk-evolve/commit/29dba096f1bc33635483558b14d2d599d2a02eb3))

- **bob**: Clarify entity count rule in learn skill
  ([#122](https://github.com/AgentToolkit/altk-evolve/pull/122),
  [`824e4d9`](https://github.com/AgentToolkit/altk-evolve/commit/824e4d95be2c49ca2bdb9f5417962f4b42551bdf))

- **claude**: Move marketplace.json back to .claude-plugin directory
  ([#115](https://github.com/AgentToolkit/altk-evolve/pull/115),
  [`18ccaa9`](https://github.com/AgentToolkit/altk-evolve/commit/18ccaa9bbd0caec97ecb6fd348a9c17f00fac81c))

- **claude**: Move marketplace.json to .claude-plugin directory
  ([#107](https://github.com/AgentToolkit/altk-evolve/pull/107),
  [`a54a976`](https://github.com/AgentToolkit/altk-evolve/commit/a54a976809b61fbba0ca773142991045a0d1117e))

- **claude**: Rename plugin to evolve-lite in marketplace.json
  ([#116](https://github.com/AgentToolkit/altk-evolve/pull/116),
  [`2cae903`](https://github.com/AgentToolkit/altk-evolve/commit/2cae90372ba5d8d9655a38a5aab7a42033f869cd))

- **entity-io**: Prevent concurrent write collisions on same slug
  ([#91](https://github.com/AgentToolkit/altk-evolve/pull/91),
  [`29dba09`](https://github.com/AgentToolkit/altk-evolve/commit/29dba096f1bc33635483558b14d2d599d2a02eb3))

- **entity-io**: Reject unsafe entity type values before joining paths
  ([#91](https://github.com/AgentToolkit/altk-evolve/pull/91),
  [`29dba09`](https://github.com/AgentToolkit/altk-evolve/commit/29dba096f1bc33635483558b14d2d599d2a02eb3))

- **entity-io**: Use start-of-line match for rationale header parsing
  ([#91](https://github.com/AgentToolkit/altk-evolve/pull/91),
  [`29dba09`](https://github.com/AgentToolkit/altk-evolve/commit/29dba096f1bc33635483558b14d2d599d2a02eb3))

- **evolve-lite**: Format entity_io.py to satisfy ruff check
  ([#134](https://github.com/AgentToolkit/altk-evolve/pull/134),
  [`43c5951`](https://github.com/AgentToolkit/altk-evolve/commit/43c5951cd4aaea27d0f5b9bb4e6b763022ab1341))

- **evolve-lite**: Guard against non-string entity type values
  ([#122](https://github.com/AgentToolkit/altk-evolve/pull/122),
  [`824e4d9`](https://github.com/AgentToolkit/altk-evolve/commit/824e4d95be2c49ca2bdb9f5417962f4b42551bdf))

- **evolve-lite**: Guard Stop hook against recursion and clean up noise
  ([#134](https://github.com/AgentToolkit/altk-evolve/pull/134),
  [`43c5951`](https://github.com/AgentToolkit/altk-evolve/commit/43c5951cd4aaea27d0f5b9bb4e6b763022ab1341))

- **evolve-lite**: Tighten learn skill to only extract high-signal guidelines
  ([#122](https://github.com/AgentToolkit/altk-evolve/pull/122),
  [`824e4d9`](https://github.com/AgentToolkit/altk-evolve/commit/824e4d95be2c49ca2bdb9f5417962f4b42551bdf))

- **justfile**: Correct volume mount path in sandbox-prompt
  ([#133](https://github.com/AgentToolkit/altk-evolve/pull/133),
  [`6e2c787`](https://github.com/AgentToolkit/altk-evolve/commit/6e2c787c3c404fecd0dcecc29b532e5aa550a242))

- **platform-integrations**: Add missing entity_io lib to Bob
  ([#105](https://github.com/AgentToolkit/altk-evolve/pull/105),
  [`a3be71d`](https://github.com/AgentToolkit/altk-evolve/commit/a3be71ddc11475151116dcdb18d4314e3932bf5b))

- **platform-integrations**: Add missing entity_io lib to Bob integration
  ([#105](https://github.com/AgentToolkit/altk-evolve/pull/105),
  [`a3be71d`](https://github.com/AgentToolkit/altk-evolve/commit/a3be71ddc11475151116dcdb18d4314e3932bf5b))

- **platform-integrations**: Clean up Bob evolve-lite skills
  ([#109](https://github.com/AgentToolkit/altk-evolve/pull/109),
  [`889c8f1`](https://github.com/AgentToolkit/altk-evolve/commit/889c8f11028c2e4f834da698527587d6197063ab))

- **platform-integrations**: Clean up trailing whitespace and add actionable recall instructions
  ([#109](https://github.com/AgentToolkit/altk-evolve/pull/109),
  [`889c8f1`](https://github.com/AgentToolkit/altk-evolve/commit/889c8f11028c2e4f834da698527587d6197063ab))

- **platform-integrations**: Handle SameFileError race in _safe_copy2
  ([#104](https://github.com/AgentToolkit/altk-evolve/pull/104),
  [`d88baf1`](https://github.com/AgentToolkit/altk-evolve/commit/d88baf1d7825b5d558b812234e490b774c922170))

- **platform-integrations**: Improve YAML parsing and error handling in install script
  ([#100](https://github.com/AgentToolkit/altk-evolve/pull/100),
  [`6bfdbbb`](https://github.com/AgentToolkit/altk-evolve/commit/6bfdbbb7b394fdcf3e4f50be4b7b73726b9e2fec))

- **platform-integrations**: Resolve Step 0 vs Step 1 ordering conflict
  ([#109](https://github.com/AgentToolkit/altk-evolve/pull/109),
  [`889c8f1`](https://github.com/AgentToolkit/altk-evolve/commit/889c8f11028c2e4f834da698527587d6197063ab))

- **platform-integrations**: Skip copy when src and dst are same file
  ([#104](https://github.com/AgentToolkit/altk-evolve/pull/104),
  [`d88baf1`](https://github.com/AgentToolkit/altk-evolve/commit/d88baf1d7825b5d558b812234e490b774c922170))

- **postgres**: Derive vector schema from embedding model
  ([#135](https://github.com/AgentToolkit/altk-evolve/pull/135),
  [`a6b7b33`](https://github.com/AgentToolkit/altk-evolve/commit/a6b7b333358bac89552aec2c17235e2938b325a8))

- **postgres**: Ensure pgvector extension before vector registration
  ([#135](https://github.com/AgentToolkit/altk-evolve/pull/135),
  [`a6b7b33`](https://github.com/AgentToolkit/altk-evolve/commit/a6b7b333358bac89552aec2c17235e2938b325a8))

- **tests**: Add postgres dependencies to dev group for testing
  ([#128](https://github.com/AgentToolkit/altk-evolve/pull/128),
  [`f2bc745`](https://github.com/AgentToolkit/altk-evolve/commit/f2bc745015f0de9e611590601df572354c1990bd))

- **tests**: Resolve linting errors and force isolated filesystem config in unit test fixture
  ([#128](https://github.com/AgentToolkit/altk-evolve/pull/128),
  [`f2bc745`](https://github.com/AgentToolkit/altk-evolve/commit/f2bc745015f0de9e611590601df572354c1990bd))

### Documentation

- Fix Codex Lite Mode installation steps in AGENTS.md
  ([#136](https://github.com/AgentToolkit/altk-evolve/pull/136),
  [`427e99d`](https://github.com/AgentToolkit/altk-evolve/commit/427e99da0200d76350f42f754967ecf82550eb22))

- Standardize cli commands to use evolve entrypoint across all readmes
  ([#128](https://github.com/AgentToolkit/altk-evolve/pull/128),
  [`f2bc745`](https://github.com/AgentToolkit/altk-evolve/commit/f2bc745015f0de9e611590601df572354c1990bd))

- Sync documentation with supported platforms (add codex, remove roo)
  ([#136](https://github.com/AgentToolkit/altk-evolve/pull/136),
  [`427e99d`](https://github.com/AgentToolkit/altk-evolve/commit/427e99da0200d76350f42f754967ecf82550eb22))

- **codex**: Align evolve-lite skill naming and expand Codex skill guidance
  ([#137](https://github.com/AgentToolkit/altk-evolve/pull/137),
  [`f962b55`](https://github.com/AgentToolkit/altk-evolve/commit/f962b550e39e001a0bb29c4b156e699cc8c03c4f))

- **config**: Document postgres backend settings
  ([#135](https://github.com/AgentToolkit/altk-evolve/pull/135),
  [`a6b7b33`](https://github.com/AgentToolkit/altk-evolve/commit/a6b7b333358bac89552aec2c17235e2938b325a8))

- **evolve-lite**: Document Stop hook UX implications and opt-out mechanism
  ([#134](https://github.com/AgentToolkit/altk-evolve/pull/134),
  [`43c5951`](https://github.com/AgentToolkit/altk-evolve/commit/43c5951cd4aaea27d0f5b9bb4e6b763022ab1341))

- **repo**: Align doc examples with integrations
  ([#131](https://github.com/AgentToolkit/altk-evolve/pull/131),
  [`58e7727`](https://github.com/AgentToolkit/altk-evolve/commit/58e77272ca42fa0591e12015d38e9b12a4cc99da))

- **repo**: Reorganize documentation structure
  ([#131](https://github.com/AgentToolkit/altk-evolve/pull/131),
  [`58e7727`](https://github.com/AgentToolkit/altk-evolve/commit/58e77272ca42fa0591e12015d38e9b12a4cc99da))

### Features

- Improve tip generation prompt with richer guidance
  ([#124](https://github.com/AgentToolkit/altk-evolve/pull/124),
  [`d373e7e`](https://github.com/AgentToolkit/altk-evolve/commit/d373e7ebb00ca0b3438aa1017961fbeb9cb5d0d8))

- Introduce Kaizen Web UI and Management Dashboard
  ([#77](https://github.com/AgentToolkit/altk-evolve/pull/77),
  [`bd67611`](https://github.com/AgentToolkit/altk-evolve/commit/bd67611088429d1b95d92731d08fec15aaee6383))

- Propagate implementation_steps through tip storage and clustering
  ([#124](https://github.com/AgentToolkit/altk-evolve/pull/124),
  [`d373e7e`](https://github.com/AgentToolkit/altk-evolve/commit/d373e7ebb00ca0b3438aa1017961fbeb9cb5d0d8))

- Remove Roo platform integration ([#130](https://github.com/AgentToolkit/altk-evolve/pull/130),
  [`8e6c76d`](https://github.com/AgentToolkit/altk-evolve/commit/8e6c76d13c111da28f486a1c3d3b716f6c39ccdc))

- **claude**: Auto-invoke learn skill after each task via stop hook
  ([#134](https://github.com/AgentToolkit/altk-evolve/pull/134),
  [`43c5951`](https://github.com/AgentToolkit/altk-evolve/commit/43c5951cd4aaea27d0f5b9bb4e6b763022ab1341))

- **evolve-lite**: Auto-invoke learn skill after each task via stop hook
  ([#134](https://github.com/AgentToolkit/altk-evolve/pull/134),
  [`43c5951`](https://github.com/AgentToolkit/altk-evolve/commit/43c5951cd4aaea27d0f5b9bb4e6b763022ab1341))

- **platform-integrations**: Add codex evolve-lite installer
  ([#111](https://github.com/AgentToolkit/altk-evolve/pull/111),
  [`215704e`](https://github.com/AgentToolkit/altk-evolve/commit/215704e56c4447bc3d74b9600ab7124880fe0d7e))

- **platform-integrations**: Add portable bash/Python installer
  ([#100](https://github.com/AgentToolkit/altk-evolve/pull/100),
  [`6bfdbbb`](https://github.com/AgentToolkit/altk-evolve/commit/6bfdbbb7b394fdcf3e4f50be4b7b73726b9e2fec))

- **platform-integrations**: Improve install.sh YAML parsing and add comprehensive tests
  ([#100](https://github.com/AgentToolkit/altk-evolve/pull/100),
  [`6bfdbbb`](https://github.com/AgentToolkit/altk-evolve/commit/6bfdbbb7b394fdcf3e4f50be4b7b73726b9e2fec))

- **release**: Automate SCRIPT_VERSION update in install.sh
  ([#100](https://github.com/AgentToolkit/altk-evolve/pull/100),
  [`6bfdbbb`](https://github.com/AgentToolkit/altk-evolve/commit/6bfdbbb7b394fdcf3e4f50be4b7b73726b9e2fec))

- **ui**: Formalize entity creation with React dropdowns and array mapped triggers
  ([#77](https://github.com/AgentToolkit/altk-evolve/pull/77),
  [`bd67611`](https://github.com/AgentToolkit/altk-evolve/commit/bd67611088429d1b95d92731d08fec15aaee6383))


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
