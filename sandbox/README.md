# Claude Code Sandbox

A Docker image for running Claude Code in a sandboxed Debian environment with Python and common Linux tools.

## Build

From the repository root:

```bash
docker build -t claude-sandbox sandbox/
```

## Run

1. Copy the sample env file and add your API key:

```bash
cp sandbox/sample.env sandbox/myenv # edit sandbox/myenv and set your credentials.
```

2. Run the container, mounting your project into `/workspace`:

```bash
docker run --rm -it --env-file sandbox/myenv -v "$(pwd)":/workspace claude-sandbox
```

3. Test that Claude Code is working:

```bash
docker run --rm --env-file sandbox/myenv claude-sandbox claude -p "who are you"
```

## Automated E2E Test

`tests/e2e/test_sandbox_learn_recall.py` exercises the full evolve-lite
learn + recall loop end-to-end inside this sandbox. It runs two Claude
sessions:

1. **Session 1** asks Claude to extract EXIF metadata from a sample photo.
   The sandbox lacks `exiftool` and `PIL`, so Claude hits dead ends and
   recovers using stdlib. The Stop hook runs `learn`, which reads the
   saved transcript and extracts a guideline.
2. **Session 2** asks a similar metadata question. Recall injects the
   guideline from session 1, so Claude should skip the failing tools and
   go straight to stdlib.

The test asserts a guideline file was produced in session 1 and that
session 2's bash commands do not invoke `exiftool` / `PIL` / `piexif` /
`exifread`.

### Prerequisites

- Build the sandbox image: `just sandbox-build claude`
- Credentials in the environment — either export `ANTHROPIC_API_KEY`
  directly, or source an env file (e.g. with
  [`dotenv`](https://github.com/bkeepers/dotenv)). The test forwards
  these vars into the container when set: `ANTHROPIC_API_KEY`,
  `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `CLAUDE_MODEL`,
  `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS`, `CLAUDE_CODE_SKIP_BEDROCK_AUTH`.

Example env file (only `ANTHROPIC_API_KEY` is required; others are
optional and used when routing through a proxy or picking a specific
model):

```bash
# Direct Anthropic API
ANTHROPIC_API_KEY=sk-ant-xxxx

# Or, via a proxy / gateway
ANTHROPIC_AUTH_TOKEN=your-token
ANTHROPIC_BASE_URL=https://your-gateway.example.com
CLAUDE_MODEL=claude-sonnet-4-6
CLAUDE_CODE_SKIP_BEDROCK_AUTH=1
```

### Run

```bash
# If creds live in an env file:
dotenv -e path/to/your.env -- \
  uv run pytest tests/e2e/test_sandbox_learn_recall.py \
    --run-e2e -m e2e -v --log-cli-level=INFO

# Or, with vars already exported:
uv run pytest tests/e2e/test_sandbox_learn_recall.py \
  --run-e2e -m e2e -v --log-cli-level=INFO
```

The `--log-cli-level=INFO` flag streams per-session progress lines live
(~4 minutes total). The test skips if Docker, the sandbox image, or
credentials are missing.

