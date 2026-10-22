"""Convert a flat 1.0 trajectory (OpenAI `messages` of a single agent, e.g. from MCP save_trajectory) into a 2.0 trajectory.

Each assistant message becomes one llm span whose input is every message before it; each tool message becomes a
tool span triggered by the llm call that requested it. Usage and timing are not known per call and are omitted."""

import json


def label(messages, i, call_of_tool, tool_span_of_call, llm_of_index):
    m = messages[i]
    if m["role"] in ("system", "developer"):
        return {"source": "system_prompt" if i == 0 else "instructions"}
    if m["role"] == "user":
        return {"source": "user"}
    if m["role"] == "assistant":
        return {"source": "previous_step", "from": [llm_of_index[i]]}
    return {"source": "tool_result", "from": [tool_span_of_call[m["tool_call_id"]]]}


def convert(flat, agent_id=None):
    messages = flat["messages"]
    agent_id = agent_id or flat.get("agent_id") or "agent"
    llm_of_index, tool_span_of_call, call_of_tool = {}, {}, {}
    for i, m in enumerate(messages):
        if m["role"] == "assistant":
            llm_of_index[i] = f"llm_{len(llm_of_index) + 1}"
            for tc in m.get("tool_calls", []):
                call_of_tool[tc["id"]] = llm_of_index[i]
        if m["role"] == "tool":
            tool_span_of_call[m["tool_call_id"]] = f"tool_{len(tool_span_of_call) + 1}"

    tools_by_name = {t["function"]["name"]: t for t in flat.get("tools") or []}
    children = []
    for i, m in enumerate(messages):
        if m["role"] == "assistant":
            has_calls = bool(m.get("tool_calls"))
            later_answer = any(x["role"] == "assistant" for x in messages[i + 1 :])
            purpose = "tool_call" if has_calls else ("answer" if not later_answer else "other")
            out = {k: v for k, v in m.items() if k in ("role", "content", "tool_calls", "refusal") and v is not None or k == "content"}
            children.append(
                {
                    "span_id": llm_of_index[i],
                    "kind": "llm",
                    "step": f"{agent_id}.{purpose}",
                    "model": flat.get("model"),
                    "input": [{**messages[j], **label(messages, j, call_of_tool, tool_span_of_call, llm_of_index)} for j in range(i)],
                    "tools": flat.get("tools") or None,
                    "output": out,
                }
            )
        elif m["role"] == "tool":
            call = next(tc for x in messages for tc in x.get("tool_calls", []) if tc["id"] == m["tool_call_id"])
            children.append(
                {
                    "span_id": tool_span_of_call[m["tool_call_id"]],
                    "kind": "tool",
                    "name": call["function"]["name"],
                    "tool_call_id": m["tool_call_id"],
                    "triggered_by": call_of_tool[m["tool_call_id"]],
                    "input": json.loads(call["function"]["arguments"]),
                    "output": m["content"],
                }
            )

    user = next(m["content"] for m in messages if m["role"] == "user")
    final = next((m["content"] for m in reversed(messages) if m["role"] == "assistant" and m.get("content")), None)
    agent = {"span_id": "agent", "kind": "agent", "agent_id": agent_id, "input": user, "output": final, "children": children}
    doc = {
        "schema_version": "2.0",
        "trace_id": flat["trace_id"],
        "namespace_id": flat.get("namespace_id"),
        "user_id": flat.get("user_id"),
        "agent_id": agent_id,
        "source": flat["source"],
        "timestamp": flat.get("timestamp"),
        "session": {"id": flat["session_id"], "turn_index": 0} if flat.get("session_id") else None,
        "agents": [{"agent_id": agent_id, "role": "specialist", "tools": sorted(tools_by_name)}],
        "tree": {
            "span_id": "turn",
            "kind": "turn",
            "start_time": flat.get("timestamp"),
            "input": user,
            "output": final,
            "children": [agent],
        },
    }
    return {k: v for k, v in doc.items() if v is not None}
