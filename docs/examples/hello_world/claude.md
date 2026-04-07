# Starter Example 2 with Evolve and Claude Code
Agentic IDEs like Claude Code often repeat the same mistakes over and over again because they start from fresh every time. Using Evolve, we can alleviate this problem.
In this tutorial, we will use Evolve and Claude Code to create a Python script to automate a simple task. Using Evolve, we will then instruct Claude Code that it should use this script in the future for similar tasks.


## Requirements
- [`uv` installed](https://docs.astral.sh/uv/getting-started/installation/)
- [Claude Code](https://claude.com/product/claude-code) installed.
- [Docker](https://docs.docker.com/get-docker/) installed.

## Step 0: Run Claude in a docker container
The following command downloads and runs the official devcontainer which is capable of running Claude Code.
!!! note "Optional"
    This is for consistency purposes so that the demo works. You can skip this step, but the demo may not work as intended.

```bash
# Copy only the .devcontainer directory from the official Claude Code repository
git clone --depth 1 --filter=blob:none --sparse https://github.com/anthropics/claude-code.git
cd claude-code
git sparse-checkout set .devcontainer
# Build and run
docker build -t my-claude .devcontainer/
docker run --rm -it my-claude bash
```

## Step 1: Install Evolve
Install Evolve by following the [installation instructions](../../installation/index.md#claude-code-plugin-marketplace)

## Step 2: Running in Claude Code
In a terminal, run:
```bash
claude
```

!!! user-message "Let's Ask Claude"
    Can you download this image?

    ![A sample image that should be downloaded](../../assets/sample.jpg)

!!! user-message "Let's Ask Claude"
    where was the photo @sample.jpg taken? use exif metadata.

!!! agent-message "A Likely Claude Response"
    I need access to some tools...

!!! user-message "Let's Ask Bob"
    summarize what steps you took, including tool calls, failed attempts, and reasoning guidelines.

!!! agent-message "A Likely Claude Response"
    Claude probably attempted to use a system utility like `exiftool`, but because we intentionally ran it in a docker container, that utility wasn't available, so Claude created a script to read the exif data instead.

!!! system-message "Learning From the Past"
    `/evolve-lite:learn` will produce new guidelines from this experience.

!!! system-message "One Eternity Later"
    Reset the conversation history using `/clear`

!!! user-message "Let's Ask Claude"
    what focal length was used to take the photo @sample.jpg? use exif metadata

!!! agent-message "A Likely Claude Response"
    This time, Claude learns from the guidelines produced by Evolve to use the generated script directly saving time.