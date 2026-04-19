---
description: Consume a job-radar LLM queue directory — read packets, write result JSONs, tell the user the ingest command.
argument-hint: "<queue_dir> | latest | <operation>"
---

Drive the job-radar `consume` flow: read a queue manifest, perform Haiku/Sonnet/Opus inference per packet locally on the user's Max plan, write `result-{id}.json` files next to each packet, then print the next-step ingest command.

**Resolve the queue dir from $ARGUMENTS:**

- If $ARGUMENTS is empty or "latest" → run `jr queue ls` and pick the topmost row whose status is `waiting` (i.e., still has pending items).
- If $ARGUMENTS looks like a directory path (contains `/llm-queue/`) → use it directly.
- If $ARGUMENTS looks like a single word (e.g. `triage`, `eval`) → run `jr queue ls`, pick the newest waiting queue whose `op` matches.
- If nothing matches, tell the user there's no work to do.

**Then, follow the SKILL.md "How to consume a queue" recipe exactly:**

1. Read `manifest.json` and `system.md` once.
2. For each item with no `result-{id}.json` yet:
   - Read `packet-{id}.md`.
   - Use `system.md` as system prompt; the packet body as user message.
   - Produce JSON matching `manifest.result_schema` (or the per-operation cheat sheet in SKILL.md if no schema is set).
   - Write the JSON to `result-{id}.json`.
3. Skip items that already have a result file — never overwrite.

When done, print **exactly**:

```
✓ wrote N results in <queue_dir>
next: jr <operation> --ingest <queue_dir>
```

The user runs the ingest command separately so the DB write stays inside Python.

User's request: $ARGUMENTS
