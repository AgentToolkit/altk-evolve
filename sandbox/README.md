# Claude Code / Codex Sandbox

Docker images for running Claude Code or Codex in a sandboxed Debian
environment with Python and common Linux tools.

## Build

From the repository root:

```bash
docker build --target claude -t claude-sandbox sandbox/
```

`sandbox/Dockerfile` has one stage per agent (`claude`, `codex`, `bob`). Without
`--target`, Docker builds the last stage, which is the Bob image.
`just sandbox-build <claude|codex|bob>` builds a single target with the image
name the recipes and tests expect.

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

`tests/e2e/test_claude_sandbox_learn_recall.py` exercises the evolve-lite
save + recall loop end to end inside this sandbox. The Claude plugin installs
no hooks and has no `learn` or `recall` skill: recall and save are Claude's
own native memory. The installer adds `.evolve/EVOLVE.md`, imported from the
workspace's `CLAUDE.md`, which asks Claude to mirror each memory it saves with
`/evolve-lite:adapt-memory` and to log the memories it consults with
`~/.claude/evolve-lite/audit_recall.py`.

The test runs `install.sh` inside the image, from a per-test copy of the
repo's `.claude-plugin/` and `platform-integrations/` mounted read-only at
`/repo`. Your checkout is not mounted, so its `.env`, `.git` and `.venv` stay out
of the sessions. The container's `~/.claude` is a per-test directory, so your own
`~/.claude` is never touched, and the plugin, native memories and transcripts
persist across the separate `docker run` sessions. It then runs three
sessions:

1. **Session 1** asks where `sample.jpg` was taken and asks Claude to remember
   how to read EXIF metadata here. The sandbox lacks `exiftool` and `PIL`, so
   Claude hits dead ends and recovers. The test asserts that a native memory
   was saved and mirrored to `.evolve/entities/<type>/<name>.md`.
2. **Session 2** asks a similar metadata question. The test asserts that the
   `recall` event in `.evolve/audit.log` for that session names the session-1
   entity. Whether Claude still tries `exiftool` is logged, not asserted.
3. **Session 3** runs `/evolve-lite:provenance`, and the test asserts that an
   influence verdict was recorded for the recalled entity.

`test_claude_sandbox_installs_evolve_lite` is a quick preflight that needs the
image but no credentials. It checks that the plugin loads (`claude plugin
list`) and that the scripts `EVOLVE.md` names exist inside the container.

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
  uv run pytest tests/e2e/test_claude_sandbox_learn_recall.py \
    --run-e2e -m e2e -v --log-cli-level=INFO

# Or, with vars already exported:
uv run pytest tests/e2e/test_claude_sandbox_learn_recall.py \
  --run-e2e -m e2e -v --log-cli-level=INFO
```

The `--log-cli-level=INFO` flag streams per-session progress lines live
(~4 minutes total). The test skips if Docker, the sandbox image, or
credentials are missing.

## Codex Automated E2E Test

`tests/e2e/test_codex_sandbox_learn_recall.py` exercises the Codex save +
recall loop end to end inside the Dockerized Codex sandbox. Codex ships no
`learn` or `recall` skill and no hooks. The installer copies `EVOLVE.md` to
`~/.codex/evolve-lite/` and points `~/.codex/AGENTS.md` at it, and the agent
recalls and saves by following it. The test installs into the container's
Codex home (the workspace's `.codex-home/`, mounted at `/codex-home`), so it
never touches your own `~/.codex`. It then runs three sessions:

1. **Session 1** asks where `sample.jpg` was taken. The sandbox has neither
   `exiftool` nor Pillow. The prompt asks the agent to save what it learned,
   since `EVOLVE.md` alone leaves saving to its judgment. Following the "Save"
   section, it writes `.evolve/entities/<type>/<name>.md`, where `<type>` is
   `user`, `feedback`, `project` or `reference`.
2. **Session 2** asks a related EXIF question. Following the "Recall" section,
   the agent's first action is to check `.evolve/entities/` and record what it
   opened with `python3 ~/.codex/evolve-lite/audit_recall.py`. The test asserts
   that session 2 appended a `recall` event to `.evolve/audit.log` naming a
   session-1 entity, and that session 2's saved trajectory carries the same
   session id. Provenance needs that id to find the trajectory.
3. **Session 3** runs the `provenance` skill, and the test asserts that an
   influence verdict was recorded for that recall.

`test_codex_sandbox_delivers_evolve_md` is a quick preflight that needs the
image but no credentials and no model call. It checks, through the image's
real entrypoint, that the `EVOLVE.md` pointer and the `save-trajectory` and
`provenance` skills are in the prompt input Codex would send.

Build the image, then load the Codex sandbox env file with `dotenv`:

```bash
just sandbox-build codex

dotenv -e ~/data/creds/codex-sandbox.env -- \
  uv run pytest tests/e2e/test_codex_sandbox_learn_recall.py \
    --run-e2e -m e2e -v --log-cli-level=INFO
```

The image pins the Codex CLI (`CODEX_VERSION` in `sandbox/Dockerfile`). To try
another release, build with
`docker build --target codex --build-arg CODEX_VERSION=<version> -t evolve-codex-sandbox sandbox/`.

The env file should export the provider credential and Codex provider settings
as environment variables, for example `CODEX_MODEL_PROVIDER`,
`CODEX_MODEL_PROVIDER_BASE_URL`, `CODEX_MODEL_PROVIDER_ENV_KEY`, and
`CODEX_MODEL_PROVIDER_WIRE_API`. The test forwards only environment variable
values into Docker; it does not mount host credential or Codex config files.

## Bob Sandbox Auth

Build the Bob image:

```bash
just sandbox-build bob
```

Authenticate once with browser SSO:

```bash
just bob-auth
```

Open the URL printed by Bob on the host machine. The recipe fixes
`SSO_PORT` and publishes it to `127.0.0.1`, so the browser callback can reach
the Bob process inside Docker. Auth state is stored in the ignored
`.bob-sandbox-home/` directory and reused by `just bob-run`, `just bob-test`,
and `just bob-prompt "..."`. The recipes also set a stable Docker hostname
because Bob's encrypted file storage derives its key from the hostname; without
that, credentials written in one `docker run --rm` session cannot be decrypted
in the next.

API-key auth with older `sk-` / `pk-` Bob keys can route Bob Shell 1.0.4 to
`prod.ibm-bob-staging.cloud.ibm.com`, which may fail with a Cloudflare 403
from inside Docker. Prefer SSO auth for this sandbox unless you have a current
Bob API key known to work with the production Bob auth backend.

## Bob Automated E2E Test

`tests/e2e/test_bob_sandbox_learn_recall.py` runs the same learn + recall flow
against the Dockerized Bob sandbox. Build the image, authenticate once, then
run pytest:

```bash
just sandbox-build bob
just bob-auth          # one-time browser SSO
uv run pytest tests/e2e/test_bob_sandbox_learn_recall.py \
  --run-e2e -m e2e -v --log-cli-level=INFO
```

The test mounts `.bob-sandbox-home/` (created by `bob-auth`) as the
container's `~/.bob`, pins the Docker hostname so Bob's encrypted file storage
decrypts across runs, and publishes `SSO_PORT` to `127.0.0.1`. It skips with a
clear message if the image isn't built or if the auth state directory is
missing. Set `BOB_HOME` / `BOB_HOSTNAME` / `BOB_SSO_PORT` env vars to override
defaults.
