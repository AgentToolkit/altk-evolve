# "Hello World" with Evolve and IBM Bob
Agentic IDEs like IBM Bob often repeat the same mistakes, because every session starts fresh. Evolve gives Bob a memory of the project, so a lesson learned in one session is reused in the next.

In this tutorial, Bob reads the EXIF metadata of a photo, hits a dead end on the way, and saves what it learned. In a new session, it recalls that lesson and goes straight to the approach that worked.

## Video Tutorial
<div class="video-wrapper">
    <iframe width="1280" height="720" src="https://www.youtube.com/embed/YlTJoSJ4eDg" title="YouTube video player" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe>
</div>

!!! note "The video shows an earlier release"
    The video was recorded in April 2026, when Evolve Lite for Bob used an `Evolve Lite` mode and explicit learn and recall skills. Current releases need neither: Bob reads Evolve's instructions in every session. The steps below follow what ships today.

## Requirements
- IBM Bob IDE installed.
- `python3` 3.9 or newer. The installer and the recall step in this tutorial run under it.

## Step 1: Create a project
Create a project directory, and save [the sample photo](../../assets/sample.jpg) into it as `sample.jpg`.
```bash
mkdir hello-world
cd hello-world
curl -fsSL -o sample.jpg https://raw.githubusercontent.com/AgentToolkit/altk-evolve/main/docs/assets/sample.jpg
```

## Step 2: Install Evolve
From the project directory, install Evolve by following the [installation instructions →](../../installation/index.md#bob-quick-start), or run:
```bash
curl -fsSL https://raw.githubusercontent.com/AgentToolkit/altk-evolve/main/platform-integrations/install.sh | bash -s -- install --platform bob --mode lite
```

The installer puts Evolve's skills, slash commands and shared library in `hello-world/.bob/`. It also makes three changes in `~/.bob/`, which apply to every project:

- It writes Evolve's instructions to `~/.bob/rules/00-evolve-lite.md`. Bob loads every file in `~/.bob/rules/` into every session, so there is no mode to switch to.
- It installs `~/.bob/evolve-lite/audit_recall.py`, the script Bob runs to record a recall.
- It adds that script's command to `tools.allowed` in your `~/.bob/settings.json`, so Bob can run it without asking. Your other settings are kept.

The instructions tell Bob two things:

- **Recall:** as its first step on a task, check `.evolve/entities/` for saved lessons that match, and record which ones it opened.
- **Save:** near the end of a task, save anything durable it learned as an entity under `.evolve/entities/<type>/`.

!!! failure "Installed into the wrong directory?"
    The installer targets the current directory, or the one you pass with `--dir`. If you ran it from somewhere else, such as your home directory or Desktop, fix it in this order:

    1. From the directory you installed into by mistake, uninstall:
        ```bash
        curl -fsSL https://raw.githubusercontent.com/AgentToolkit/altk-evolve/main/platform-integrations/install.sh | bash -s -- uninstall --platform bob
        ```
    2. Run the install command again from `hello-world`, or pass `--dir /path/to/hello-world`.

    The order matters. `uninstall` removes the stray copy, but it also undoes the three `~/.bob/` changes described above, which every project uses. Step 2 puts them back.

## Step 3: Ask Bob
Open the `hello-world` directory in IBM Bob IDE and start a new chat.

!!! user-message "Let's Ask Bob"
    Where was the photo sample.jpg taken? Use EXIF metadata.

!!! agent-message "A Likely Bob Response"
    Bob checks `.evolve/entities/` first and finds nothing, because the project is new. It then reaches for a tool such as `exiftool` or Pillow. If that isn't installed, Bob falls back to a short Python script that reads the EXIF data with the standard library, and reports where the photo was taken.

!!! system-message "Learning From the Past"
    Following Evolve's instructions, Bob saves the lesson as an entity under `.evolve/entities/<type>/`. For example, it might note that EXIF data can be read with the Python standard library when no image tools are installed. Bob decides on its own whether a task taught it anything worth keeping. If it saved nothing, ask it to:

!!! user-message "Let's Ask Bob"
    Save what you learned from this task to your Evolve memory.

## Step 4: Ask again in a new session
Start a new chat, so that Bob has no memory of the first conversation.

!!! user-message "Let's Ask Bob"
    What focal length was used to take the photo sample.jpg? Use EXIF metadata.

!!! agent-message "A Likely Bob Response"
    This time Bob finds the saved lesson in `.evolve/entities/`, records that it consulted it, and goes straight to the approach that worked, skipping the dead ends.

## What Evolve wrote
```text
hello-world/
  .bob/                          # Evolve's skills, slash commands and shared library
  .evolve/
    entities/
      project/
        read-exif-with-stdlib.md # the saved lesson (Bob picks the type and name)
    audit.log                    # a "recall" line for each session that consulted an entity
```

Each entity is a Markdown file with `type` and `trigger` frontmatter. The trigger describes when the lesson applies, and it's what Bob matches against during recall. You can read, edit or delete entities at any time.

## Troubleshooting
- **Bob doesn't seem to use Evolve.** Check that `~/.bob/rules/00-evolve-lite.md` exists. If Bob was already running when you installed, start a new chat. If that doesn't help, restart IBM Bob IDE.
- **Check the install.** From the project directory, run:
  ```bash
  curl -fsSL https://raw.githubusercontent.com/AgentToolkit/altk-evolve/main/platform-integrations/install.sh | bash -s -- status
  ```
  For a lite install, every Bob row should show ✓ except `custom_modes.yaml` and `mcp (full mode)`, which belong to full mode.
