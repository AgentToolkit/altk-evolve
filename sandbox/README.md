# Claude Code Sandbox

A Docker image for running Claude Code in a sandboxed Debian environment with Python and common Linux tools.

## Build

```bash
docker build -t claude-sandbox .
```

## Run

1. Copy the sample env file and add your API key:

```bash
cp sample.env .env # edit .env and set your credentials.
```

2. Run the container, mounting your project into `/workspace`:

```bash
docker run --rm -it --env-file .env -v "$(pwd)":/workspace claude-sandbox
```

3. Test that Claude Code is working:

```bash
docker run --rm --env-file .env claude-sandbox claude -p "who are you"
```

