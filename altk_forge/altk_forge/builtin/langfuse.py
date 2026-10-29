"""Convert a Langfuse trace export (GET /api/public/traces/{id}) of a LangGraph agent into a 2.0 trajectory.

It is the reference the LLM is shown when it writes a converter for a new kind of trace (converters.writer), and the
converter `python -m altk_forge.cli.convert langfuse` runs.

Kept spans: the turn, agents (LangGraph nodes), LLM calls, tools, retrievers. Framework plumbing (Runnable*,
parsers, __start__, routing functions) is dropped and its children are attached to the nearest kept ancestor.

Inferred by heuristics (native instrumentation records them exactly):
  step           <agent>.<purpose>, purpose from the agent's role and the call's output
  input source   what each request message is, matched against the user text, the handoff and tool outputs
  response_format  schemas sent as tools that nothing executed
  error/outcome  a failure (error) or a result that worked but deserves attention (outcome: empty_result, discarded),
                 each with detected_by = source (Langfuse level), tool (its own status field) or converter (heuristic)
  retries        a call whose request is repeated by the next call of the same step had its output discarded:
                 it gets outcome.type=discarded and the retry gets triggered_by pointing at it
Memory spans appear only when the agent's memory access is instrumented."""

import json
import re

import jsonschema

from ..trajectory.spans import walk
from ..trajectory.validate import graph_agent_node

EMPTY_RESULT = re.compile(r"^\s*(no results? found|\[\s*\])?\s*$", re.I)


# Plain-text tool output that reports its own failure. Matched only at the start, so documents *about* errors
# (e.g. Db2 docs on SQL error codes) are not flagged.
ERROR_TEXT = re.compile(r"^\s*(error\b|exception\b|traceback\b|SQL\d{4}N\b)", re.I)


FAILED_STATUS = {"error", "failed", "failure"}


EMPTY_STATUS = {"no_results", "no_data", "empty", "not_found"}


ERROR_CODE = re.compile(r"\b(SQL\d{4}[NW]|SQLSTATE[ =:]*\d{5})\b")


RETRIEVER_NAME = re.compile(r"search|retriev|vector", re.I)


# A step the agent declared (memory.step) is used only when it is a valid step of the agent the call ran in.
STEP = re.compile(r"^[^.\s]+\.(route|plan|query_rewrite|tool_call|answer|reflect|summarize|extract|other)(\.[A-Za-z0-9_-]+)?$")


SPAN_ORDER = [
    "span_id",
    "kind",
    "start_time",
    "end_time",
    "agent_id",
    "step",
    "name",
    "provider",
    "model",
    "model_parameters",
    "prompt",
    "operation",
    "store",
    "tool_call_id",
    "triggered_by",
    "input",
    "tools",
    "response_format",
    "output",
    "finish_reason",
    "time_to_first_token_ms",
    "usage",
    "memory",
    "error",
    "outcome",
    "children",
]


def by_time(observations):
    return sorted(observations, key=lambda o: o["startTime"])


def as_text(value):
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("content"), str):
        return value["content"]
    return json.dumps(value, ensure_ascii=False)


def usage_of(obs):
    u, details = obs.get("usage") or {}, obs.get("usageDetails") or {}
    if not u.get("total"):
        return None
    return {
        "prompt_tokens": u.get("input"),
        "completion_tokens": u.get("output"),
        "total_tokens": u.get("total"),
        "cached_prompt_tokens": details.get("input_cache_read"),
        "reasoning_tokens": details.get("output_reasoning"),
        "cost_usd": obs.get("calculatedTotalCost") or None,
    }


def to_tool_call(tc):
    """LangChain tool_call {name, args, id} -> OpenAI ToolCall."""
    if "function" in tc:
        return {"id": tc["id"], "type": "function", "function": tc["function"]}
    return {
        "id": tc["id"],
        "type": "function",
        "function": {"name": tc["name"], "arguments": json.dumps(tc.get("args", {}), ensure_ascii=False)},
    }


def llm_messages(raw):
    """Langfuse generation input -> (messages, tool schemas). Langfuse logs tool schemas as role=tool messages."""
    messages, schemas = [], []
    for m in raw or []:
        role, content = m["role"], m.get("content")
        if role == "tool" and isinstance(content, dict) and content.get("type") == "function":
            schemas.append(content)
            continue
        msg = {"role": role, "content": as_text(content) or ""}
        if role == "assistant" and m.get("tool_calls"):
            msg["tool_calls"] = [to_tool_call(tc) for tc in m["tool_calls"]]
            msg["content"] = msg["content"] or None
        if role == "tool":
            msg["tool_call_id"] = m.get("tool_call_id") or ""
        messages.append(msg)
    return messages, schemas


def llm_output(raw):
    """Assistant message, or None when the call produced nothing (e.g. it failed). Structured output that arrives
    already parsed (a dict or list, e.g. a router's {"next": ...}) is kept as its JSON text."""
    if not raw or not (raw.get("content") or raw.get("tool_calls")):
        return None
    content = raw.get("content")
    msg = {"role": "assistant", "content": (content if isinstance(content, str) else as_text(content)) or None}
    if raw.get("tool_calls"):
        msg["tool_calls"] = [to_tool_call(tc) for tc in raw["tool_calls"]]
    return msg


def llm_error(obs):
    if obs.get("level") != "ERROR":
        return None
    message = obs.get("statusMessage") or "LLM call failed"
    code = re.search(r"Error code: (\d{3})", message)
    return {"type": "llm_error", "message": message[:500], "code": f"HTTP {code.group(1)}" if code else None, "detected_by": "source"}


def json_status(text):
    """`status` / `error` fields of a tool's JSON result, if it returns one."""
    try:
        value = text if isinstance(text, (dict, list)) else json.loads(text or "")
    except (json.JSONDecodeError, TypeError):
        return None, None
    if not isinstance(value, dict):
        return None, None
    return str(value.get("status") or "").lower() or None, value.get("error")


def span_status(obs, text):
    """(error, outcome) of a tool/retriever span: a failure, or a result that worked but found nothing."""
    out = obs.get("output")
    status, error = json_status(text)
    message = (text or "").strip()[:500] or "(empty output)"
    code = ERROR_CODE.search(text or "")
    code = code.group(1) if code else None
    if obs.get("level") == "ERROR":
        return {"type": "tool_error", "message": obs.get("statusMessage") or message, "code": code, "detected_by": "source"}, None
    if (isinstance(out, dict) and out.get("status") == "error") or status in FAILED_STATUS or error:
        return {"type": "tool_error", "message": message, "code": code, "detected_by": "tool"}, None
    if status in EMPTY_STATUS:
        return None, {"type": "empty_result", "message": message, "detected_by": "tool"}
    if EMPTY_RESULT.match(text or ""):
        return None, {"type": "empty_result", "message": message, "detected_by": "converter"}
    if status is None and ERROR_TEXT.match(text or ""):
        kind = "invalid_arguments" if re.match(r"^\s*SQL0(204|206|104|408)N", text or "") else "tool_error"
        return {"type": kind, "message": message, "code": code, "detected_by": "converter"}, None
    return None, None


def documents(raw, text):
    docs = raw.get("artifact") if isinstance(raw, dict) else raw
    if isinstance(docs, list):
        return [
            {
                "doc_id": str(d.get("id") or d.get("doc_id") or i),
                "score": d.get("score"),
                "source": d.get("source") or d.get("url"),
                "content": d.get("content") or d.get("page_content"),
            }
            for i, d in enumerate(docs)
            if isinstance(d, dict)
        ]
    return [] if EMPTY_RESULT.match(text or "") else [{"doc_id": "0", "content": text}]


def user_text_of(trace_input):
    for msgs in (trace_input.get("db_messages") or {}).values():
        humans = [m for m in msgs if m.get("type") == "human"]
        if humans:
            return humans[-1]["content"]
    for m in reversed(trace_input.get("messages") or []):
        if m.get("type") == "human":
            return m["content"]
    raise ValueError("no user message in trace input")


def state_messages(state):
    """Messages in a LangGraph state or state update. Besides `messages`, this graph keeps a per-database
    channel `db_messages` ({db key: message or [messages]}), where some agents write their answers."""
    found = list(state.get("messages") or [])
    for key, value in (state.get("db_messages") or {}).items():
        if key.startswith("__"):
            continue
        found += value if isinstance(value, list) else [value]
    return [m for m in found if isinstance(m, dict)]


def graph_update(obs):
    """LangGraph Command output of a node: (messages added to state, goto targets)."""
    out = obs.get("output") if isinstance(obs.get("output"), dict) else {}
    goto = out.get("goto")
    return state_messages(out.get("update") or {}), [goto] if isinstance(goto, str) else list(goto or [])


def final_answer(trace, agent_outputs):
    """The answer shown to the user: the last AI message of the final state that an agent wrote."""
    candidates = [m for m in state_messages(trace.get("output") or {}) if m.get("type") == "ai" and m.get("content")]
    by_agent = [m for m in candidates if m.get("name")]
    if by_agent:
        # State channels are not ordered across each other; the agent that ran last wrote the answer.
        order = {name: i for i, name in enumerate(agent_outputs)}
        return max(by_agent, key=lambda m: order.get(m["name"], -1))["content"]
    return candidates[-1]["content"] if candidates else None


def kind_of(o, graph_id):
    if o["id"] == graph_id:
        return "turn"
    if o["type"] == "GENERATION":
        return "llm"
    if o["type"] == "RETRIEVER" or (o["type"] == "TOOL" and RETRIEVER_NAME.search(o["name"])):
        return "retriever"
    if o["type"] == "TOOL":
        return "tool"
    if graph_agent_node(o):
        return "agent"
    return None  # plumbing: kept out, its children go to the nearest kept ancestor


def convert(trace, host=None):
    obs = trace["observations"]
    children = {}
    for o in obs:
        children.setdefault(o.get("parentObservationId"), []).append(o)
    ids = {o["id"] for o in obs}
    graph = next(
        o for o in obs if o.get("parentObservationId") not in ids and (o.get("metadata") or {}).get("ls_integration") == "langgraph"
    )
    user_text = user_text_of(trace["input"])
    executed = {o["name"] for o in obs if o["type"] in ("TOOL", "RETRIEVER")}
    agents, handoffs = {}, []  # handoffs: (agent span_id, goto targets, text handed over), in time order

    def kept_children(o):
        out = []
        for c in by_time(children.get(o["id"], [])):
            out.extend([c] if kind_of(c, graph["id"]) else kept_children(c))
        return by_time(out)

    def build(o, agent_id):
        kind = kind_of(o, graph["id"])
        s = {"span_id": o["id"], "kind": kind, "start_time": o["startTime"], "end_time": o.get("endTime")}
        if kind == "turn":
            s.update(input=user_text)
        elif kind == "agent":
            agent_id = o["name"]
            delegator = next((h for h in reversed(handoffs) if agent_id in h[1]), None)
            added, _ = graph_update(o)
            s.update(
                agent_id=agent_id,
                triggered_by=delegator[0] if delegator else None,
                input=delegator[2] if delegator and delegator[2] else user_text,
                output=added[-1]["content"] if added else None,
            )
        elif kind == "llm":
            messages, schemas = llm_messages(o.get("input"))
            meta, out = o.get("metadata") or {}, o.get("output") or {}
            tools = [x for x in schemas if x["function"]["name"] in executed]
            formats = [x for x in schemas if x["function"]["name"] not in executed]
            s.update(
                provider=meta.get("ls_provider"),
                model=o.get("model"),
                model_parameters=o.get("modelParameters") or None,
                prompt={"name": o["promptName"], "version": o.get("promptVersion")} if o.get("promptName") else None,
                input=messages,
                tools=tools or None,
                response_format={"name": formats[0]["function"]["name"], "schema": formats[0]["function"].get("parameters", {})}
                if formats
                else None,
                output=llm_output(out),
                finish_reason=(out.get("response_metadata") or {}).get("finish_reason"),
                time_to_first_token_ms=round(o["timeToFirstToken"] * 1000) if o.get("timeToFirstToken") is not None else None,
                usage=usage_of(o),
                error=llm_error(o),
                _agent=agent_id,
                memory=memory_of(meta),
            )
        else:  # tool / retriever
            text = as_text(o.get("output"))
            error, outcome = span_status(o, text)
            s.update(
                name=o["name"],
                tool_call_id=(o.get("metadata") or {}).get("tool_call_id"),
                input=o.get("input") if isinstance(o.get("input"), dict) else None,
                output=documents(o.get("output"), text) if kind == "retriever" else text,
                error=error,
                outcome=outcome,
            )
        kids = [build(c, agent_id) for c in kept_children(o)]
        if kids:
            s["children"] = kids
        if kind == "agent":
            finish_agent(s, agents, handoffs, user_text, graph_update(o)[1])
        return s

    tree = build(graph, None)
    tree["output"] = final_answer(trace, [a for a in agents])
    tree = reorder(tree)
    return prune(
        {
            "schema_version": "2.0",
            "trace_id": trace["id"],
            "user_id": trace.get("userId"),
            "agent_id": (trace.get("name") or "").split(":")[0] or None,
            "source": {
                "kind": "langfuse",
                "project": trace.get("projectId"),
                "uri": f"{host.rstrip('/')}{trace['htmlPath']}" if host and trace.get("htmlPath") else None,
                "environment": trace.get("environment"),
                "tags": sorted(trace.get("tags") or []),
            },
            "timestamp": trace.get("timestamp"),
            "session": session_of(trace),
            "task": {"type": (trace.get("metadata") or {}).get("task_type"), "name": (trace.get("metadata") or {}).get("task_name")},
            "experiment": experiment_of(trace),
            "agents": [{**a, "tools": sorted(a["tools"]), "can_delegate_to": sorted(a["can_delegate_to"])} for a in agents.values()],
            "tree": tree,
            "expected": expected_of(trace.get("metadata") or {}),
            "scores": scores_of(trace.get("scores") or []),
        }
    )


def finish_agent(agent_span, agents, handoffs, user_text, goto):
    """Once an agent's subtree is built: register the agent, link its tool calls, label its LLM inputs, name its steps."""
    agent_id = agent_span["agent_id"]
    agent = agents.setdefault(agent_id, {"agent_id": agent_id, "role": "specialist", "tools": set(), "can_delegate_to": set()})
    agent["can_delegate_to"] |= {g for g in goto if g != "__end__"}
    own = sorted(
        (x for x in walk(agent_span) if x is not agent_span and x.get("_agent", agent_id) == agent_id and x["kind"] != "agent"),
        key=lambda x: x["start_time"],
    )
    llms = [x for x in own if x["kind"] == "llm"]
    results = [x for x in own if x["kind"] in ("tool", "retriever", "memory")]
    agent["tools"] |= {x["name"] for x in results if x["kind"] != "memory"}
    # A supervisor routes: it hands off to other agents and does no tool work itself.
    agent["role"] = (
        "supervisor"
        if agent["can_delegate_to"] and not agent["tools"]
        else (
            "finalizer"
            if not agent["can_delegate_to"] and not agent["tools"] and re.search(r"final|summar", agent_id, re.I)
            else "specialist"
        )
    )
    for r in results:
        caller = next((x for x in reversed(llms) if x["start_time"] <= r["start_time"]), None)
        if caller:
            r["triggered_by"] = caller["span_id"]
    handoff_text = agent_span["input"] if agent_span.get("triggered_by") else None
    for i, llm in enumerate(llms):
        declared = (llm.get("memory") or {}).get("step", "")
        llm["step"] = (
            declared
            if STEP.match(declared) and declared.split(".")[0] == agent_id
            else f"{agent_id}.{purpose_of(llm, agent['role'], llms[i + 1 :])}"
        )
        label_input(llm, user_text, handoff_text, agent_span.get("triggered_by"), results, llms[:i])
        del llm["_agent"]
    mark_retries(llms)
    handoffs.append((agent_span["span_id"], goto, agent_span["output"]))


def request_of(llm):
    return [(m["role"], m.get("content"), json.dumps(m.get("tool_calls"))) for m in llm["input"]]


def mark_retries(llms):
    """A call sent again with the same request (possibly extended, e.g. with a forced output schema) means the
    agent discarded the first output. Record why, when the retry's response_format says so."""
    for first, retry in zip(llms, llms[1:]):
        req = request_of(first)
        if first.get("error") or not first["output"] or first["step"] != retry["step"] or request_of(retry)[: len(req)] != req:
            continue
        reason = schema_violation(first["output"], retry.get("response_format") or first.get("response_format"))
        first["outcome"] = {
            "type": "discarded",
            "detected_by": "converter",
            "message": f"The agent discarded this output and retried the call{f': {reason}' if reason else '.'}",
        }
        retry["triggered_by"] = first["span_id"]


def schema_violation(output, response_format):
    """First reason the output does not satisfy the response_format schema, or None when it cannot be told."""
    try:
        value = json.loads(output.get("content") or "")
    except json.JSONDecodeError as e:
        return f"content is not valid JSON ({e.msg})"
    if not response_format:
        return None
    error = next(jsonschema.Draft202012Validator(response_format["schema"]).iter_errors(value), None)
    if not error:
        return None
    where = "/".join(map(str, error.absolute_path))
    return (
        f"{where} = {json.dumps(error.instance, ensure_ascii=False)[:80]} is not allowed by {response_format['name']}"
        if error.validator == "enum"
        else f"{where or 'output'}: {error.message[:160]}"
    )


def memory_of(meta):
    """The memory contract the agent recorded on the generation (observability/llm_memory.py in the agent): the
    namespace the call reads guidelines from, its step and the query it selects them by. None when not recorded."""
    if not meta.get("memory_namespace"):
        return None
    m = {
        "namespace": meta["memory_namespace"],
        "step": meta.get("memory_step"),
        "query": meta.get("memory_query"),
        "guideline_ids": [str(x) for x in meta.get("memory_guideline_ids") or []],
        "enabled": meta.get("memory_enabled"),
    }
    return {k: v for k, v in m.items() if v not in (None, "")}


def purpose_of(llm, role, later_llms):
    if role == "supervisor":
        return "route"  # every call of a router serves the routing decision
    fmt = (llm.get("response_format") or {}).get("name", "")
    if re.search(r"query", fmt, re.I):
        return "query_rewrite"
    if re.search(r"route|next|intent|classif", fmt, re.I):
        return "route"
    # A tool call is a request the runtime executes; a structured-output reply (response_format) is an answer.
    if (llm["output"] or {}).get("tool_calls") and llm.get("tools"):
        return "tool_call"
    if llm.get("tools") and llm.get("error"):
        return "tool_call"  # failed before answering, while tools were on offer
    return "answer" if not later_llms else "other"


def label_input(llm, user_text, handoff_text, handoff_span, results, earlier_llms):
    """Set `source` (and `from`) on every request message."""
    for i, m in enumerate(llm["input"]):
        content = (m.get("content") or "").strip()
        src, frm = None, []
        if m["role"] in ("system", "developer"):
            src = "system_prompt" if i == 0 else "instructions"
        elif m["role"] == "assistant":
            src = "previous_step"
            frm = [
                x["span_id"]
                for x in earlier_llms
                if x["output"] and x["output"].get("tool_calls") == m.get("tool_calls") and x["output"].get("content") == m.get("content")
            ]
        elif m["role"] == "tool":
            src = "tool_result"
            frm = [r["span_id"] for r in results if r.get("tool_call_id") == m.get("tool_call_id")]
        elif content == user_text.strip():
            src = "user"
        elif handoff_text and handoff_text.strip() in content:
            src, frm = "handoff", [handoff_span]
        else:
            queried = [r for r in results if ((r.get("input") or {}).get("query") or "").strip() == content]
            hits = [r for r in results if r.get("output") and contains(content, r["output"])]
            if not hits and "no result" in content.lower():
                hits = [r for r in results if r["kind"] == "retriever" and not r["output"]]
            if queried:
                src, frm = "previous_step", sorted({r["triggered_by"] for r in queried if r.get("triggered_by")})
            elif hits:
                src = "retrieved" if any(r["kind"] == "retriever" for r in hits) else "tool_result"
                frm = [r["span_id"] for r in hits]
            elif user_text.strip() in content:
                src = "instructions"  # the user text wrapped in a template
            else:
                src = "injected_context"
        m["source"] = src
        if frm:
            m["from"] = frm


def contains(text, output):
    """Whether a tool/retriever output appears in a prompt message, tolerating the JSON re-encoding agents apply."""
    if isinstance(output, list):
        output = " ".join(d.get("content") or "" for d in output)
    probe = (output or "").strip()[:120]
    if not probe:
        return False
    variants = {probe, probe.replace('\\"', '"'), json.dumps(probe)[1:-1], probe.encode().decode("unicode_escape", "ignore")}
    return any(v and v in text for v in variants)


def session_of(trace):
    if not trace.get("sessionId"):
        return None
    history = (trace.get("input") or {}).get("conversational_history") or []
    # Langfuse does not store the turn index; the number of earlier user messages passed in is the best estimate.
    return {"id": trace["sessionId"], "turn_index": len([m for m in history if m.get("type") == "human"])}


def experiment_of(trace):
    meta = trace.get("metadata") or {}
    if not meta.get("benchmark_run"):
        return None
    dataset = next((t for t in trace.get("tags") or [] if "_" in t and "." not in t), None)
    return {"dataset": dataset, "release": trace.get("release") or trace.get("environment")}


def expected_of(meta):
    expected = {}
    if meta.get("expected_output"):
        expected["final_answer"] = meta["expected_output"].strip()
    if meta.get("expected_trajectory"):
        expected["agent_path"] = {"match": "in_order", "agents": meta["expected_trajectory"]}
    if meta.get("expected_tool_calls"):
        expected["tool_calls"] = {"match": "any_order", "calls": [{"name": n} for n in meta["expected_tool_calls"]]}
    if expected:
        expected["provenance"] = {"kind": "dataset", "name": meta.get("task_name")}
    return expected or None


def scores_of(scores):
    out = []
    for s in scores:
        # Langfuse does not store a score's range; this pipeline uses 0-100 except for *_f1 metrics.
        rng = [0, 1] if s["name"].endswith("_f1") or s.get("dataType") == "BOOLEAN" else [0, 100]
        out.append(
            {
                "metric": re.sub(r"[^a-z0-9_]", "_", s["name"].lower()),
                "value": s.get("value") if s.get("dataType") in ("NUMERIC", "BOOLEAN") else None,
                "label": s.get("stringValue"),
                "range": rng,
                "target": {"span_id": s["observationId"]} if s.get("observationId") else None,
                "explanation": s.get("comment") or None,
                "evaluator": {
                    "kind": "human" if s.get("source") == "ANNOTATION" else "external",
                    "name": f"langfuse:{s.get('source', '').lower()}",
                },
            }
        )
    return out


def reorder(span):
    s = {k: span[k] for k in SPAN_ORDER if k in span}
    if "children" in s:
        s["children"] = [reorder(c) for c in s["children"]]
    return s


def prune(value):
    """Drop null/empty fields. Kept: an assistant message's required `content: null`, and `output`, where an empty
    result (a retriever that found nothing) is information."""
    if isinstance(value, dict):
        return {k: prune(v) for k, v in value.items() if v not in (None, [], {}) or k in ("content", "output")}
    if isinstance(value, list):
        return [prune(v) for v in value]
    return value
