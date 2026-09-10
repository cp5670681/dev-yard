# WEB_GRILL_ROUND

This invocation is **one focused frontier round** for the web console. Compute this round, write the files below, then stop.

## Convergence & Budget Guidelines

- **Strict Rounds Cap**:
  - Small tasks: 1 round (1–3 questions).
  - Medium tasks: 1–2 rounds (3–5 questions/round).
  - Large / Complex tasks: 2–3 rounds (5–8+ questions/round).
  - Hard cap: 3 rounds maximum.
- **Adaptive Volume & Thematic Grouping**: For large requirements, expand all P0/P1 blockers in one round rather than fragmenting across many rounds. Group questions by module/domain when question count is high.
- **Sensible Defaults**: For minor implementation details or edge cases, make reasonable best-practice assumptions, document them in `GRILL.md` under `### 默认假设与实现细节 (Sensible Defaults)`, and do not ask them.

## Steps

1. Read `REQUIREMENT.md`, source clones, and existing `GRILL.md` (previous answers stay).
2. If previous answers have settled the core flow/contracts, or if round count reaches the target (or hard cap of 3 rounds) without new P0 blockers, write `{"done": true, "round": N, "questions": []}` to `reqs/<JIRA>/.grill-round.json` and stop.
3. Otherwise, append this round's `❓` / `➡️` questions to `reqs/<JIRA>/GRILL.md`.
4. Write `reqs/<JIRA>/.grill-round.json` with the schema below.
5. Stop. Do not invent user answers. Do not write SPEC.md or TICKETS.md.

If `GRILL.md` already has answers for the last round, recompute the **next** frontier from those answers.

When the frontier is empty, or when core architecture/business agreements are settled, write `{"done": true, "round": N, "questions": []}` and stop. Next command is `dev-yard spec <JIRA>` (the web UI offers Spec).

## `.grill-round.json`

```json
{
  "done": false,
  "round": 1,
  "intro": "one-line context for this round",
  "questions": [
    {
      "id": "Q1",
      "title": "short title",
      "body": "full question, including facts the user needs",
      "options": [
        {"id": "A", "label": "closed choice"},
        {"id": "B", "label": "other closed choice"}
      ],
      "suggested": "A",
      "suggested_text": "the ➡️ recommendation, one or two sentences"
    }
  ]
}
```

- Closed decision: 2–5 `options`. `suggested` is the recommended option id. `suggested_text` is the ➡️ sentence.
- Open decision: `options` is `[]`. Put the recommendation in `suggested_text`.
- `id` is `Q1`, `Q2`, … within this round. `title` is never empty.
- `done: true` only when there is nothing left to ask.
