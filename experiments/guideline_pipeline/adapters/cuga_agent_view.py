"""Reconstruct the main agent's conversation from a CUGA task recording.

CUGA records a multi-agent run (a main code-writing agent plus helper agents for
tool discovery, reflection, pre-execute verification and final-answer classification).
The tracker stores each LLM call's input only as one flattened LangChain string
(``prompts[2]``: ``System: …\\nHuman: …\\nAI: …``), so the original message list is
recovered by splitting on those role markers.

Known heuristic limit: a line inside a message's own text that starts with
``System: ``, ``Human: ``, ``AI: `` or ``Tool: `` is treated as a role boundary
and produces an extra split-out message. This can happen when tool output or
execution results include that text (e.g. ``print("AI: ok")`` or logs containing
the markers). The agent-view mode exists to keep helper-agent output in user
turns rather than attribute it to the main agent, and this edge case is a
narrow exception where tool text can look like an assistant turn. If CUGA ever
records the message list directly (e.g. via an ``on_chat_model_start`` callback),
the split would become unnecessary.

Vendored from ``cuga-eval/benchmarks/helpers/cuga_trajectory.py`` with two
deliberate differences, both inside this file (nothing is monkey-patched elsewhere):

- **Option A** (in ``agent_messages``): after dropping the few-shot example
  conversation, strip the CUGA ``## User Context`` block (supervisor name/email/
  phone, current datetime) from the task user message. Downstream in altk-evolve
  the first user message of ``messages`` becomes the IR's ``task`` and the
  standard prompt's ``Task:`` line, so the strip keeps PII out of the generation
  prompt and the clustering key. The agent-view messages become one line shorter
  than what the agent actually received, which is an accepted approximation.

- **Output source** (in ``agent_turns``): take each turn's recorded assistant
  output from the ``Raw_Assistant_Response`` step's own ``data`` field rather
  than from ``prompts[-1]``. When the final-answer classifier returns
  ``auto_continue: true``, CUGA's tracker merges its prompts into the next
  ``Raw_Assistant_Response`` step, so ``prompts[0]`` would otherwise be the
  classifier's ``{"auto_continue": true}``. The step's own ``data`` is always
  the agent's full response (reasoning plus any ``\\`\\`\\`python`` block or
  plain-text answer), independent of what ``prompts`` carries.

Stdlib only, so this module imports cleanly inside altk-evolve's virtualenv.
"""

from __future__ import annotations

import re

ROLE_MARKER_RE = re.compile(r"(?:^|\n)(System|Human|AI|Tool): ")
ROLE_NAMES = {"System": "system", "Human": "user", "AI": "assistant", "Tool": "tool"}

AGENT_STEP = "Raw_Assistant_Response"
AVAILABLE_VARIABLES_MARKER = "\n\n## Available Variables\n\n"
USER_CONTEXT_MARKER = "\n\n## User Context"
NEW_VARIABLES_MARKER = "## New Variables Created:"


def split_messages(rendered: str) -> list[dict]:
    """Split a rendered LangChain prompt string into role/content messages."""
    marks = list(ROLE_MARKER_RE.finditer(rendered))
    messages = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(rendered)
        messages.append({"role": ROLE_NAMES[m.group(1)], "content": rendered[m.end() : end].strip("\n")})
    return messages


def call_input_output(step: dict) -> tuple[list[dict], str]:
    """(input messages, output) of an LLM-call step, as the LangChain callback stored them."""
    prompts = step.get("prompts") or []
    inputs = [p["value"] for p in prompts if p["role"] != "assistant"]
    outputs = [p["value"] for p in prompts if p["role"] == "assistant"]
    rendered = max(inputs, key=len) if inputs else ""
    return split_messages(rendered) or [{"role": "system", "content": rendered}], (outputs[0] if outputs else "")


def task_message_index(messages: list[dict], intent: str) -> int:
    """Index of the user message carrying the real task.

    Everything between the system prompt and this message is CUGA's built-in
    example conversation. Matches the LAST user message whose first line equals
    the first line of the intent exactly, so a few-shot example whose body
    starts with the same phrase (``"Book a flight"`` vs.
    ``"Book a flight for the example user"``) cannot select ahead of the real
    task, and a multi-line intent still finds its message (CUGA formats the task
    as ``<intent>\\n\\n## User Context…``, so comparing on the first line is
    enough).
    """
    intent_lines = intent.strip().splitlines()
    if not intent_lines:
        raise ValueError("task message not found (intent missing or not matched)")
    head = intent_lines[0].strip()
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m["role"] == "user" and m["content"].strip().split("\n", 1)[0].strip() == head:
            return i
    raise ValueError("task message not found (intent missing or not matched)")


def strip_available_variables(content: str) -> str:
    cut = content.find(AVAILABLE_VARIABLES_MARKER)
    return content[:cut] if cut >= 0 else content


def strip_user_context(content: str) -> str:
    """Remove the ``## User Context`` block only, keeping any sections after it.

    CUGA formats the task message as ``<intent>\\n\\n## User Context\\n…`` and may
    append further ``## <section>`` headings. Cutting at the first ``\\n\\n## ``
    after the User Context marker preserves those later sections.
    """
    start = content.find(USER_CONTEXT_MARKER)
    if start < 0:
        return content
    # Look for the next heading of the same level after the block body starts.
    body_start = start + len(USER_CONTEXT_MARKER)
    next_heading = content.find("\n\n## ", body_start)
    if next_heading < 0:
        return content[:start]
    return content[:start] + content[next_heading:]


def _turn_output(step: dict) -> str:
    """The agent's recorded output for this ``Raw_Assistant_Response`` step.

    The step's own ``data`` field is the authoritative record of what the agent
    produced on this turn; ``prompts[-1]`` can carry an adjacent helper agent's
    output when CUGA merges prompts across calls (see module docstring)."""
    data = step.get("data")
    if isinstance(data, str) and data.strip():
        return data
    # Fallback for recordings missing the data field: use the LangChain assistant
    # prompt. This path is not reached on CUGA 0.3.x bundles.
    return call_input_output(step)[1]


def agent_turns(trajectory: dict) -> list[dict]:
    """One entry per main-agent call, with what happened after it in that turn.

    Each turn: ``step_index``, ``input`` (actual input messages of the call),
    ``output`` (the agent's response text), ``code`` (``Assistant_code.data``,
    or ``None`` for a plain-text reply), ``execution_output`` (``User_output.data``),
    ``reflection`` (the reflection LLM's STATUS/NEXT output) and ``next_user_message``
    (``User_return.data``). ``EvaluationResult`` is never read.

    Raises ``ValueError`` when the last ``Raw_Assistant_Response`` step has no
    ``prompts``: ``agent_messages`` depends on that step's recovered input to
    carry the whole conversation, and silently ending one turn short would hide
    agent work from the generator while the outcome still described the full run.
    """
    steps = trajectory.get("steps", [])
    all_agent_steps = [i for i, s in enumerate(steps) if s.get("name") == AGENT_STEP]
    agent_idx = [i for i in all_agent_steps if steps[i].get("prompts")]
    if all_agent_steps and (not agent_idx or agent_idx[-1] != all_agent_steps[-1]):
        raise ValueError(f"last {AGENT_STEP} step has no prompts; cannot recover the full agent conversation")
    turns = []
    for n, i in enumerate(agent_idx):
        end = agent_idx[n + 1] if n + 1 < len(agent_idx) else len(steps)
        messages, _ = call_input_output(steps[i])
        turn = {
            "step_index": i,
            "input": messages,
            "output": _turn_output(steps[i]),
            "code": None,
            "execution_output": None,
            "reflection": None,
            "next_user_message": None,
        }
        for s in steps[i + 1 : end]:
            name = s.get("name")
            if name == "Assistant_code":
                turn["code"] = s.get("data")
            elif name == "User_output":
                turn["execution_output"] = s.get("data")
            elif name == "User_return":
                turn["next_user_message"] = s.get("data")
                if s.get("prompts"):
                    turn["reflection"] = call_input_output(s)[1]
        turns.append(turn)
    return turns


def agent_messages(trajectory: dict, drop_examples: bool = False, strip_last_available_variables: bool = False) -> list[dict]:
    """The main agent's conversation as OpenAI-format messages.

    Each main-agent call sees the whole conversation so far, so the input of the
    last call already is the full history. ``agent_messages`` takes that input and
    appends the last call's output as the final assistant message. With
    ``drop_examples=True`` the ``## User Context`` block is also stripped from the
    task message (see module docstring, Option A).

    The result has shape ``[system, user, (assistant, user)*, assistant]``.
    """
    turns = agent_turns(trajectory)
    if not turns:
        raise ValueError(f"no {AGENT_STEP} step with prompts in trajectory")
    last = turns[-1]
    messages = [dict(m) for m in last["input"]]
    if not last["output"]:
        raise ValueError(f"last {AGENT_STEP} step has no recorded output")
    if not messages or messages[0]["role"] != "system":
        raise ValueError("could not split the rendered prompt into messages")
    if drop_examples:
        messages = messages[:1] + messages[task_message_index(messages, trajectory.get("intent", "")) :]
        # Option A: strip the User Context block from the task message so the IR's
        # task line stays the clean intent (see module docstring).
        if len(messages) >= 2 and messages[1]["role"] == "user":
            messages[1] = {"role": "user", "content": strip_user_context(messages[1]["content"])}
    if strip_last_available_variables and messages[-1]["role"] == "user":
        messages[-1] = {"role": "user", "content": strip_available_variables(messages[-1]["content"])}
    messages.append({"role": "assistant", "content": last["output"]})
    return messages
