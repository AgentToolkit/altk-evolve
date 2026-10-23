"""Cut a trajectory into the pieces sent to memory: one slice per llm call.

A slice is a short conversation (task + the input the model had, its output, what the tools returned, an evaluation
note) in a namespace per pipeline, agent and step, so the guidelines learned from a step are retrieved for that step
only. Every call is sent; a call whose lesson belongs elsewhere (discarded output, failed call, a finding that says to
fix the prompt or code) carries that in its evaluation. The viewer shows exactly these slices (GET .../slices)."""

import hashlib
import json
import re

from .guidelines import call_guidelines
from .spans import walk_with_parent
from .text import clip, msg_text, readable, try_json

# Which turn score judges which kind of step.
STEP_SCORE = {"answer": "correctness", "tool_call": "tool_call_f1", "route": "trajectory", "query_rewrite": "correctness"}


FIX_ELSEWHERE = {"prompt", "code", "routing", "retrieval", "tool_description", "context_assembly"}


PURPOSE = {
    "route": "choose the next agent",
    "plan": "plan the task",
    "query_rewrite": "write search queries",
    "tool_call": "choose a tool and its arguments",
    "answer": "write the answer",
    "reflect": "check the result",
    "summarize": "summarize context",
    "extract": "extract structured data",
    "other": "intermediate step",
}


ERROR = {
    "tool_error": "tool returned an error",
    "invalid_arguments": "invalid arguments",
    "timeout": "timed out",
    "permission_denied": "permission denied",
    "rate_limited": "rate limited",
    "parse_error": "output could not be parsed",
    "llm_error": "llm call failed",
    "guardrail": "blocked by a guardrail",
    "other": "error",
}


def norm(score):
    """Score normalised to 0-1 (1 = best), from its range and direction; None for label-only scores."""
    if not score or score.get("value") is None:
        return None
    v = score["value"]
    lo, hi = score.get("range") or ([0, 100] if v > 1 else [0, 1])
    x = max(0.0, min(1.0, (v - lo) / ((hi - lo) or 1)))
    return 1 - x if score.get("higher_is_better") is False else x


def fmt_score(s):
    return str(s.get("label")) if s.get("value") is None else f"{round(float(s['value']), 2):g}"


def structured(span):
    out = span.get("output")
    if not out:
        return None
    rf = span.get("response_format")
    forced = rf and next((tc for tc in out.get("tool_calls") or [] if tc["function"]["name"] == rf.get("name")), None)
    return try_json(forced["function"]["arguments"]) if forced else try_json(out.get("content"))


def namespace_of(prefix, agent, purpose):
    """Milvus collection names allow letters, digits and _ only; parts are joined with __."""

    def part(x):
        return re.sub(r"^_+|_+$", "", re.sub(r"[^a-z0-9]+", "_", str(x or "unknown").lower())) or "x"

    ns = "__".join(part(x) for x in (prefix or "pipeline", agent, purpose))
    return ns if re.match(r"^[a-z_]", ns) else f"n_{ns}"


def slices(doc, pipeline_id, namespace=None, prefix=None):
    """Every llm call of the trajectory as a slice. `namespace` sends all slices to one namespace; otherwise it is
    <prefix>__<agent>__<purpose> with the pipeline id as the default prefix."""
    spans, agent_of, order = {}, {}, []
    for s, parent in walk_with_parent(doc["tree"]):
        spans[s["span_id"]] = s
        order.append(s)
        agent_of[s["span_id"]] = s.get("agent_id") if s["kind"] == "agent" else agent_of.get(parent["span_id"]) if parent else None
    num = {s["span_id"]: i for i, s in enumerate((x for x in order if x["kind"] not in ("turn", "agent")), 1)}
    turn_scores = {x["metric"]: x for x in doc.get("scores") or [] if not x.get("target")}
    findings = doc.get("findings") or []
    ctx = dict(
        doc=doc,
        spans=spans,
        agent_of=agent_of,
        num=num,
        turn_scores=turn_scores,
        findings=findings,
        pipeline=pipeline_id,
        namespace=namespace,
        prefix=prefix or pipeline_id,
    )
    return [_slice(s, ctx) for s in order if s["kind"] == "llm"]


def _slice(s, c):
    doc, spans = c["doc"], c["spans"]
    agent, purpose = c["agent_of"].get(s["span_id"]), s["step"].split(".")[1] if "." in s["step"] else s["step"]
    mem = s.get("memory") or {}
    # The agent named the namespace it reads this step's guidelines from; its memories go to the same one.
    ns = c["namespace"] or mem.get("namespace") or namespace_of(c["prefix"], agent, purpose)
    st = structured(s) or {}
    st = st if isinstance(st, dict) else {}
    effects = [x for x in spans.values() if x["kind"] in ("tool", "retriever", "memory") and x.get("triggered_by") == s["span_id"]]
    retry = next((x for x in spans.values() if x["kind"] == "llm" and x.get("triggered_by") == s["span_id"]), None)

    # ---- what went wrong with the call itself. Every call is sent: altk-evolve decides what is worth a guideline;
    # this only tells it (and the viewer) what happened.
    def is_trace(r):
        return not r.get("trace_id") or r["trace_id"] == doc["trace_id"]

    fixes = [
        f
        for f in c["findings"]
        if (f.get("fix_target") or {}).get("component") in FIX_ELSEWHERE
        and any(is_trace(r) and r.get("span_id") == s["span_id"] for r in (f.get("symptom") or []) + (f.get("cause") or []))
    ]
    problem = None  # what went wrong with the call, if anything (sent all the same)
    if (s.get("outcome") or {}).get("type") == "discarded":
        reason = re.sub(r"^.*?retried the call:?\s*", "", s["outcome"].get("message") or "").strip(" .")
        reason = f"Reason: {reason}." if reason else ""
        as_retry = f" as #{c['num'][retry['span_id']]}" if retry else ""
        problem = f"The agent threw this output away and retried the call{as_retry}. {reason}".strip()
    elif s.get("error"):
        e = s["error"]
        code = f", {e['code']}" if e.get("code") else ""
        problem = f"The call failed ({ERROR.get(e.get('type'), e.get('type'))}{code})" + (
            f": {clip(e.get('message'), 300)}" if e.get("message") else "."
        )
    elif fixes:
        problem = f"A finding points at the {', '.join(f['fix_target']['component'] for f in fixes)}: " + " ".join(
            f.get("summary") or "" for f in fixes
        )

    # ---- local checks: deterministic, from the trajectory and its gold data
    checks, exp = [], doc.get("expected") or {}
    if purpose == "route":
        nxt = st.get("next") or st.get("agent") or st.get("route")
        path = (exp.get("agent_path") or {}).get("agents") or []
        want = path[path.index(agent) + 1] if agent in path and path.index(agent) + 1 < len(path) else None
        if nxt and want:
            checks.append(
                {"ok": nxt == want, "text": f"routed to {nxt}, as expected" if nxt == want else f"routed to {nxt}; expected {want}"}
            )
    called = [x["name"] for x in effects if x["kind"] != "memory"]
    if purpose == "tool_call" and exp.get("tool_calls") and called:
        allowed = {x["name"] for x in exp["tool_calls"]["calls"]}
        extra = list(dict.fromkeys(n for n in called if n not in allowed))
        checks.append(
            {
                "ok": not extra,
                "text": f"called {', '.join(extra)}, not among the expected {', '.join(sorted(allowed))}"
                if extra
                else f"called {', '.join(dict.fromkeys(called))}, as expected",
            }
        )
    empty = [
        x for x in effects if (x.get("outcome") or {}).get("type") == "empty_result" or (x["kind"] == "retriever" and not x.get("output"))
    ]
    failed = [x for x in effects if x.get("error")]
    if empty:
        checks.append({"ok": False, "text": f"{', '.join(x['name'] for x in empty)} found nothing"})
    if failed:
        checks.append(
            {"ok": False, "text": ", ".join(f"{x['name']}: {ERROR.get(x['error'].get('type'), x['error'].get('type'))}" for x in failed)}
        )
    if purpose == "answer":
        feeds = [m for m in s.get("input") or [] if m.get("source") in ("retrieved", "tool_result")]

        def bad(f):
            return f and (f.get("outcome") or f.get("error") or (f["kind"] == "retriever" and not f.get("output")))

        if not feeds:
            checks.append({"ok": None, "text": "answered without tool or search results"})
        elif all(all(bad(spans.get(i)) for i in m.get("from") or []) for m in feeds):
            checks.append({"ok": False, "text": "every tool or search result it got was empty or failed"})

    # ---- the score that judges this step, and the label derived from it
    metric = STEP_SCORE.get(purpose)
    score = c["turn_scores"].get(metric) if metric else None
    x = norm(score) if score else None
    label = "unknown" if x is None else "success" if x >= 0.999 else "failure"
    if label == "success" and purpose != "answer" and any(ch["ok"] is False for ch in checks):
        label = "failure"
    if purpose == "route" and checks:
        label = "success" if checks[0]["ok"] else "failure"
    if problem:  # a failed, discarded or flagged call did not succeed, whatever the turn scored
        label = "failure"

    # ---- the conversation memory receives
    inp = s.get("input") or []
    handoff = next((m for m in inp if m.get("source") == "handoff"), None)
    parts = [m for m in inp if m.get("source") not in ("system_prompt", "user", "handoff")]
    sys_msg = next((m for m in inp if m.get("source") == "system_prompt"), None)
    given = call_guidelines(s)
    model = f", model {s['model']}" if s.get("model") else ""
    shown = [
        f"[{m.get('source')}{', ' + m['role'] if m.get('role') else ''}] "
        f"{clip(msg_text(m) or (json.dumps(m['tool_calls']) if m.get('tool_calls') else ''), 600)}"
        for m in parts
    ]
    lines = [
        f"User question: {doc['tree'].get('input')}",
        f"Task from the delegating agent: {clip(msg_text(handoff), 1200)}" if handoff else None,
        f"Step: {s['step']} (agent {agent}, {PURPOSE.get(purpose, purpose)}){model}.",
        "Input the model received besides its system prompt:\n" + "\n".join(shown) if shown else None,
    ]
    messages = [{"role": "user", "content": "\n\n".join(x for x in lines if x)}]
    out = s.get("output") or {}
    if out.get("tool_calls"):
        messages.append({"role": "assistant", "content": out.get("content") or None, "tool_calls": out["tool_calls"]})
    else:
        messages.append({"role": "assistant", "content": clip(readable(out.get("content")) or out.get("content") or "")})
    for e in (x for x in effects if x["kind"] != "memory"):
        o = e.get("output")
        body = "\n---\n".join(d.get("content") or "" for d in o) if isinstance(o, list) else o
        messages.append(
            {
                "role": "tool",
                "tool_call_id": e.get("tool_call_id") or e["span_id"],
                "content": clip(body or ((e.get("error") or {}).get("message") if e.get("error") else "(empty)")),
            }
        )
    final = readable(doc["tree"].get("output"))
    rng = (score or {}).get("range") or [0, 1]
    review = [
        "[Evaluation of this step, added by Forge; not written by the agent]",
        f"Label: {label}" + (f" ({metric} {fmt_score(score)} of {rng[1]} for the whole turn)" if score else "") + ".",
        f"- PROBLEM: {problem}" if problem else None,
        *(f"- {'OK' if ch['ok'] is True else 'PROBLEM' if ch['ok'] is False else 'NOTE'}: {ch['text']}." for ch in checks),
        f"Expected answer: {clip(exp['final_answer'], 800)}"
        if exp.get("final_answer") and purpose in ("answer", "query_rewrite")
        else None,
        f"Answer the user got: {clip(final, 800)}" if purpose == "answer" and final else None,
        f"Evaluator: {clip(score['explanation'], 800)}" if score and score.get("explanation") else None,
        ("Guidelines from memory the model was given:\n" + "\n".join(f"- {g}" for g in given)) if given else None,
    ]
    messages.append({"role": "assistant", "content": "\n".join(x for x in review if x), "review": True})

    task_id = f"{doc['trace_id']}:{s['span_id']}"
    trajectory = {
        "messages": [{k: v for k, v in m.items() if k != "review"} for m in messages],
        # No context_messages: altk-evolve takes the task from their first user message when they are there, and
        # then sees only the bare question instead of the step's description (who asked, what the model was given),
        # judges a correct answer to it routine and generates nothing. The agent's own memory query stays in
        # metadata.memory_query.
        "trace_id": task_id,
        "model": s.get("model"),
        "metadata": {
            k: v
            for k, v in {
                "task_id": task_id,
                "trace_id": doc["trace_id"],
                "span_id": s["span_id"],
                "step": s["step"],
                "agent_id": agent,
                "session_id": (doc.get("session") or {}).get("id"),
                "user_id": doc.get("user_id"),
                "pipeline": c["pipeline"],
                "label": label,
                "memory_query": mem.get("query"),
                "task": (doc.get("task") or {}).get("name") or (doc.get("task") or {}).get("type"),
                "source": "forge",
            }.items()
            if v is not None
        },
        "batch": {"source": "forge", "conversation_id": doc["trace_id"], "batch_id": s["span_id"], "scope": c["pipeline"]},
    }
    # altk-evolve skips a batch it has processed before (same scope, source, conversation, batch and revision). The
    # revision is the content of what is sent, so a slice that changed (a new converter, a fix here) is processed
    # again, while the same slice sent twice is still recognised.
    content = {k: trajectory[k] for k in ("messages", "context_messages", "tools") if k in trajectory}
    trajectory["batch"]["revision"] = hashlib.sha256(
        json.dumps(content, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()[:16]
    if s.get("tools"):
        trajectory["tools"] = s["tools"]
    if label != "unknown":
        trajectory["outcome"] = {
            "success": label == "success",
            "failed_checks": [ch["text"] for ch in checks if ch["ok"] is False],
            **({"detail": clip(score["explanation"], 800)} if score and score.get("explanation") else {}),
        }
    extra = {
        "step": s["step"],
        "trace_id": doc["trace_id"],
        "span_id": s["span_id"],
        "pipeline": c["pipeline"],
        "step_score": {"metric": metric, "value": score.get("value"), "range": score.get("range")} if score else None,
        "prompt": s.get("prompt"),
        "guidelines_given": given or None,
        "namespace_source": "agent" if mem.get("namespace") and not c["namespace"] else "forge",
        "guideline_ids_given": mem.get("guideline_ids") or None,
        "system_prompt": {"chars": len(msg_text(sys_msg)), "note": "left out: the same for every call of this step"} if sys_msg else None,
    }
    return {
        "span_id": s["span_id"],
        "num": c["num"].get(s["span_id"]),
        "step": s["step"],
        "agent": agent,
        "purpose": purpose,
        "namespace_id": ns,
        "label": label,
        "checks": checks,
        "problem": problem,
        "messages": messages,
        "trajectory": trajectory,
        "extra": extra,
    }
