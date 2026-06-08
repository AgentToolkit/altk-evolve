# Evolve — shared, auditable memory

You already have native, self-directed memory: you decide what to recall at the
start of a task and what to save at the end, on your own judgment. Evolve adds
only the two things native memory lacks — sharing and provenance — as two
lightweight lifecycle steps. It does not change how you recall or save.

## After you save a memory

When you save a native memory this turn, invoke /evolve-lite:adapt-memory
to mirror it into the shared evolve store (`./.evolve/entities/`) so the fact is
shareable with the team and auditable like every other evolve entity. Do this
once per memory you saved.

## Record what you consulted

After you read or consult memories this turn, log which ones you actually opened,
so the value of this memory can be measured over time. Run:

```
python3 ~/.claude/evolve-lite/audit_recall.py <file> [<file> ...]
```

Pass the memory files you read this turn (space-separated paths, relative to the
project root). Skip this step entirely if you consulted no memories. If the
command prints a line beginning `evolve-session:`, include that line once,
verbatim, somewhere in your reply — it lets later analysis tie this session to
what you recalled.
