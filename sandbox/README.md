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
cp sandbox/sample.env sandbox/.env # edit sandbox/.env and set your credentials.
```

2. Run the container, mounting your project into `/workspace`:

```bash
docker run --rm -it --env-file sandbox/.env -v "$(pwd)":/workspace claude-sandbox
```

3. Test that Claude Code is working:

```bash
docker run --rm --env-file sandbox/.env claude-sandbox claude -p "who are you"
```

