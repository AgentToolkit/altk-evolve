# Starter Example with Evolve and Codex CLI
Agentic IDEs like Codex CLI often repeat the same mistakes over and over again because they start from fresh every time. Using Evolve, we can alleviate this problem.
In this tutorial, we will use Evolve and Codex CLI to create a Python script to automate a simple task. Using Evolve, we will then instruct Codex CLI that it should use this script in the future for similar tasks.

<div class="video-wrapper">
    <iframe width="1280" height="720" src="https://www.youtube.com/embed/IBc59bLjdi8" title="YouTube video player" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe>
</div>

## Requirements
- [`uv` installed](https://docs.astral.sh/uv/getting-started/installation/)
- [Codex CLI](https://codex.com/product/codex-code) installed.
- [Docker](https://docs.docker.com/get-docker/) installed.

## Step 0: Run Codex in a docker container
The following commands download and run the official node image which is capable of installing Codex CLI.
!!! note "Optional"
    This is for consistency purposes so that the demo works. You can skip this step, but the demo may not work as intended.

```bash
docker run --rm -it \
    --security-opt seccomp=unconfined \
    --security-opt apparmor=unconfined \
    node:22-slim bash
```
Then inside the docker container, install codex:
```bash
apt-get update && apt-get install -y --no-install-recommends curl ca-certificates python3
npm i -g @openai/codex
```

## Step 1: Install Evolve-Lite Plugin Repo
Install Evolve by following the [installation instructions](../../installation/index.md#codex-code-plugin-marketplace)

## Step 2: Running in Codex CLI
In a terminal, run:
```bash
codex # Sign in with a device code if in the docker container
```
Install the Evolve-Lite plugin
```bash
# in codex
/plugins
```
Select the `Evolve Lite` plugin and install it.

!!! user-message "Let's Ask Codex"
    Can you download this image?

    ![A sample image that should be downloaded](../../assets/sample.jpg)

!!! user-message "Let's Ask Codex"
    where was the photo @tmp/sample.jpg taken? use exif metadata.

!!! agent-message "A Likely Codex Response"
    I need access to some tools...

!!! user-message "Let's Ask Codex"
    summarize what steps you took, including tool calls, failed attempts, and reasoning guidelines.

!!! agent-message "A Likely Codex Response"
    Codex probably attempted to use a system utility like `exiftool`, but because we intentionally ran it in a docker container, that utility wasn't available, so Codex created a script to read the exif data instead.

!!! user-message "Learning From the Past"
    `$evolve-lite:learn` will produce new guidelines from this experience, if they were not generated already.

!!! system-message "One Eternity Later"
    Reset the conversation history using `/clear`

!!! user-message "Let's Ask Codex"
    what focal length was used to take the photo @tmp/sample.jpg? use exif metadata

!!! agent-message "A Likely Codex Response"
    This time, Codex learns from the guidelines produced by Evolve to use the generated script directly saving time.
    
