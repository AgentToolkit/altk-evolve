# "Hello World" with Evolve and IBM Bob
In this tutorial, we will create a simple scaffolding project using `uv` and set up IBM Bob with Evolve.

## Requirements
- [`uv` installed](https://docs.astral.sh/uv/getting-started/installation/)
- IBM Bob IDE installed.

## Step 1: Create a new project using `uv`
Create a new project directory and initialize it with `uv init`.
```bash
mkdir hello-world
cd hello-world
uv init
```

## Step 2: Install Evolve
Install Evolve by following the [installation instructions](../../installation/index.md), or run:
```bash
curl -fsSL https://raw.githubusercontent.com/AgentToolkit/altk-evolve/main/platform-integrations/install.sh | bash -s -- install --platform bob --mode lite
```
## Step 3: Project Setup
Open the project directory in IBM Bob IDE, and switch Bob's mode to `Evolve Lite`

## Step 4: Testing Evolve with Bob
If we test with the following example, at this point Bob doesn't know that this project is managed with `uv`, so Bob will probably get it wrong. It's entirely possible that Bob will still figure it out, so alternatively come up with something simple that Bob will get wrong.
```
Set up the python environment for this project
```

### A Likely Bob Response
Bob should run some Evolve Lite skills to attempt to retrieve relevant guidelines from memory, and find nothing because it's a new project.
Bob will likely attempt to use `python` or `python3`'s `venv` to create a virtual environment.
```bash
python3 -m venv .venv
```
### Recovering and Remembering
We respond,
```
This is incorrect. This project is supposed to be managed by `uv`.
```
At this point, Bob will correct itself and eventually run `uv sync` and correctly run `uv run main.py`.
Bob should then run some Evolve Lite skills to save the learned correction into memory.
If it does not, the `/evolve:learn` command can be used to manually run the skill.

In the future, Bob should remember via Evolve-Lite skills that `uv` is used for this project and not make the same mistake. This can be tested in this toy project by deleting the `.venv` directory and the `uv.lock` file and trying the same utterance again.
