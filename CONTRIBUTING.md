# How to Contribute to Evolve

Evolve welcomes external contributions of all kinds. Whether you are fixing bugs, improving existing functionality, or adding new evolution-focused capabilities, we’re glad to work with you.

## Did you find a bug?

* Please file an issue at https://github.com/AgentToolkit/altk-evolve/issues.

## Types of contributions

* Fixing bugs
* Improving existing Evolve components
* Adding new components or evolution strategies
* Improving documentation, examples, or tests

## Setting up for development

We use https://docs.astral.sh/uv/ as our package and project manager. To install it, please follow the official installation guide:
https://docs.astral.sh/uv/getting-started/installation/

## Create a virtual environment

You can use `uv` to create a virtual environment (if it doesn’t already exist) and install all project dependencies:

```bash
uv venv && source .venv/bin/activate
uv sync
```

## Using a specific Python version

```bash
uv venv --python 3.12 && source .venv/bin/activate
uv sync
```

## Adding a new dependency

```bash
uv add <package_name>
```

## Coding style guidelines

We use https://pre-commit.com to ensure consistency across commits and pull requests.

```bash
pre-commit install
```

Or:

```bash
pre-commit run --all-files
```

## Running Tests

The test suite is organized into 4 cleanly isolated tiers depending on infrastructure requirements:

1. **Default Local Suite**
   Runs both fast logic tests (`unit`) and filesystem script verifications (`platform_integrations`).
   ```bash
   uv run pytest
   ```

2. **Unit Tests (Only)**
   Fast, fully-mocked tests verifying core logic and offline pipeline schemas.
   ```bash
   uv run pytest -m unit
   ```

3. **Platform Integration Tests**
   Fast filesystem-level integration tests verifying local tool installation and idempotency.
   ```bash
   uv run pytest -m platform_integrations
   ```

4. **End-to-End Infrastructure Tests**
   Heavy tests that autonomously spin up a background Phoenix server and simulate full agent workflows.
   ```bash
   uv run pytest -m e2e --run-e2e
   ```
   *(See [the Low-Code Tracing Guide](docs/guides/low-code-tracing.md#end-to-end-verification) for more details).*

5. **LLM Evaluation Tests**
   Tests needing active LLM inference to test resolution pipelines (requires LLM API keys).
   ```bash
   uv run pytest -m llm
   ```

## Adding new components to Evolve

Evolve builds on Agent Lifecycle Toolkit concepts with a focus on agent evolution, adaptation, and improvement over time.

1. Identify the evolution purpose of your component.
2. Add a new package under the appropriate module.
3. Update `pyproject.toml` and `uv.lock`.
4. Add a `README.md` describing usage and configuration.
5. Add tests alongside the module in `tests/`.
6. Update relevant documentation files for discoverability.

## Detecting secrets

```bash
make detect-secrets
```

Or manually:

```bash
uv pip install --upgrade "git+https://github.com/ibm/detect-secrets.git@master#egg=detect-secrets"
detect-secrets scan --update .secrets.baseline
detect-secrets audit .secrets.baseline
```

## Developer Certificate of Origin (DCO)

All commits must be signed off using:

```bash
git commit -s -m <msg>
```
