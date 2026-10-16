# Starter Example with Evolve and Claude Code
Agentic IDEs like Claude Code often repeat the same mistakes over and over again because they start from fresh every time. Using Evolve, we can alleviate this problem.
In this tutorial, we will use Evolve and Claude Code to create a Python script to automate a simple task. Using Evolve, we will then instruct Claude Code that it should use this script in the future for similar tasks.

## Video Tutorial
The plugin has changed since this video was recorded and no longer has a `/evolve-lite:learn` skill. Where the video and the steps below differ, follow the steps.

<div class="video-wrapper">
    <iframe width="1280" height="720" src="https://www.youtube.com/embed/XIlYA79pYp4" title="YouTube video player" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe>
</div>

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
Evolve Lite needs more than the plugin: the installer also puts `.evolve/EVOLVE.md` in your project, imports it from the project's `CLAUDE.md`, and installs two small scripts under `~/.claude/evolve-lite/`. Installing only the plugin (`claude plugin install`) loads the skills but none of the rest, and `/evolve-lite:doctor` then reports `NOT_INSTALLED`.

Clone Evolve somewhere it can stay (the plugin is read from the clone), create a project directory, and run the installer for it. See the [installation instructions →](../../installation/index.md#claude-code-quick-start) for details.
```bash
git clone --depth 1 https://github.com/AgentToolkit/altk-evolve.git ~/altk-evolve
mkdir exif-demo && cd exif-demo && git init
~/altk-evolve/platform-integrations/install.sh install --platform claude --dir .
```

## Step 2: Running in Claude Code
In the project directory, run:
```bash
claude
```

!!! note "Allow the import"
    On the first session, Claude may ask whether to allow the project's `CLAUDE.md` to import `.evolve/EVOLVE.md`. Allow it, or Evolve's instructions never reach Claude. Run `/evolve-lite:doctor` to check that the import is loading.

!!! user-message "Let's Ask Claude"
    Can you download this image?

    ![A sample image that should be downloaded](../../assets/sample.jpg)

!!! user-message "Let's Ask Claude"
    where was the photo @sample.jpg taken? use exif metadata.

!!! agent-message "A Likely Claude Response"
    I need access to some tools...

!!! user-message "Let's Ask Claude"
    remember for future sessions how to read EXIF metadata from photos in this project, including which tools did and did not work.

!!! system-message "Learning From the Past"
    Claude saves this as one of its own native memories. Evolve's instructions (`.evolve/EVOLVE.md`) then have it run `/evolve-lite:adapt-memory`, which mirrors the memory into the project as `.evolve/entities/<type>/<name>.md`, where you can inspect or edit it. Claude may also save a memory like this without being asked.

!!! system-message "One Eternity Later"
    Reset the conversation history using `/clear`

!!! user-message "Let's Ask Claude"
    what focal length was used to take the photo @sample.jpg? use exif metadata

!!! agent-message "A Likely Claude Response"
    This time, Claude reads the memory it saved and goes straight to the approach that worked, saving time. Because it consulted that memory, it also runs `python3 ~/.claude/evolve-lite/audit_recall.py <type>/<name>`, which records the recall in `.evolve/audit.log`.

!!! system-message "Did It Help?"
    Later, run `/evolve-lite:provenance`. It reads the recall events in `.evolve/audit.log` and each session's transcript, and records whether the recalled memory was followed in that session.
