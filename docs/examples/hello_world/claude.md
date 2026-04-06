# Starter Example 2 with Evolve and Claude Code
Agentic IDEs like Claude Code often repeat the same mistakes over and over again because they start from fresh every time. Using Evolve, we can alleviate this problem.
In this tutorial, we will use Evolve and Claude Code to create a Python script to automate a simple task. Using Evolve, we will then instruct Claude Code that it should use this script in the future for similar tasks.


## Requirements
- [`uv` installed](https://docs.astral.sh/uv/getting-started/installation/)
- [Claude Code](https://claude.com/product/claude-code) installed.
- [Docker](https://docs.docker.com/get-docker/) installed.

## Step 1: Run Claude in a docker container
The following command downloads the official devcontainer which is capable of running Claude Code. This is for consistency purposes so that the demo works. You can skip this step, but the demo may not work as intended.
```bash
# Copy only the .devcontainer directory from the official Claude Code repository
git clone --depth 1 --filter=blob:none --sparse https://github.com/anthropics/claude-code.git
cd claude-code
git sparse-checkout set .devcontainer
# Build and run
docker build -t my-claude .devcontainer/
docker run --rm -it my-claude bash
```

## Step 2: Install Evolve
Install Evolve by following the [installation instructions](../../installation/index.md#claude-code-plugin-marketplace)

## Step 3: Running in Claude Code
In a terminal, run:
```bash
claude
```
Ask Claude to download the following image:

![A sample image that should be downloaded](../../assets/sample.jpg)
And then ask:
```
where was the photo @sample.jpg taken? use exif metadata.
```
It will ask for permission to use some tools, which should be accepted.

Have it produce a summary:
```
summarize what steps you took, including tool calls, failed attempts, and reasoning guidelines.
```
### A Likely Claude Response
Claude probably attempted to use a system utility like `exiftool`, but because we intentionally ran it in a docker container, that utility wasn't available, so Claude created a script to read the exif data instead.

We can learn from this by running a command:
```
/evolve-lite:learn
```
which will produce new guidelines from this experience.

## Step 4: Try Again
Reset the conversation history using
```
/clear
```

Ask a similar question:
```
what focal length was used to take the photo @sample.jpg? use exif metadata
```

### A Likely Claude Response
This time, Claude learns from the guidelines produced by Evolve to use the generated script directly saving time.