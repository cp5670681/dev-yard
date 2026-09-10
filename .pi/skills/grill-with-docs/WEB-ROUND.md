# WEB_GRILL_ROUND

This invocation is **one focused frontier round** for the web console. Compute this round, write the files below, then stop.

## Convergence & Budget Guidelines

- **Limit Rounds**: Target 1–2 rounds (max 3 rounds).
- **Limit Questions**: Ask at most 2–4 critical (P0/P1) architectural/business blocker questions per round.
- **Sensible Defaults**: For minor implementation details or edge cases, make reasonable best-practice assumptions, document them in `GRILL.md` under `### 默认假设与实现细节 (Sensible Defaults)`, and do not ask them.

## Steps

1. Read `REQUIREMENT.md`, source clones, and existing `GRILL.md` (previous answers stay).
2. If previous answers have settled the core flow/contracts, or if round count reaches the target without new P0 blockers, write `{"done": true, "round": N, "questions": []}` to `reqs/<JIRA>/.grill-round.json` and stop.
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
