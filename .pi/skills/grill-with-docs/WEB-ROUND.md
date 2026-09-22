# WEB_GRILL_ROUND

This invocation is **one focused frontier round** for the web console. Compute this round, write the files below, then stop.

## Rounds

- Small tasks: 1 round. Medium tasks: 1–2 rounds. Large tasks: 2–3 rounds. Hard cap: 3 rounds.
- **Round count is a ceiling, not a quota.** Ask the questions the requirement genuinely
  needs — never pad a round to hit a target size, but never shrink the frontier to reach zero.
- **Zero questions is legal but must be proven, not assumed.** It is the right outcome only
  when the frontier is genuinely empty (a small, fully-specified requirement) **and** you have
  written the `### Frontier 清点` block below. For a large or cross-team requirement, a
  zero-question first round is usually an under-explored frontier — re-read `REQUIREMENT.md`
  and the source clones before concluding zero.

## Do not ask what research can answer (hard rule)

`REQUIREMENT.md`, `GRILL.md`「关键事实（已自查）」, the source clones, schemas, and
existing requirements are **your job to read, not the user's job to answer**. A question
whose answer already lives in any of those is a defect — put it in `### 默认假设与实现细节
(Sensible Defaults)` instead. The runner drops any question that fails to prove otherwise.

Qualification gate — a question may only be asked if **all four** hold:

1. The answer is **not** already stated in `REQUIREMENT.md` or any acceptance criterion.
2. The answer is **not** already listed in `GRILL.md`「关键事实（已自查）」.
3. It is **not** derivable from the source code, DB schema, or existing requirements.
4. Getting it wrong would cause rework, or it is irreversible / cross-team / a product-intent call.
   **A similar existing feature to copy does not demote a cross-team or product-intent
   decision to a default** — a new AI/external function, request/response schema, callback
   path, or a silent/ambiguous product rule is always a question (unless check #1 already
   resolves it because the requirement fixes it).

If any check fails, it is not a question. In particular, these are **anti-patterns**
(observed failure modes):

- "Which table/column should the field live in?" when the requirement already says
  "reuse the field used by X" — the answer is X's table, go read it.
- "Do we need to sync to the other database?" — copy the sync path of the feature the
  requirement says to reuse; if that is not possible, say so as a Sensible Default.
- "What is the permission role?" — reuse the role the closest existing feature uses.
- Recommending an option that **contradicts `REQUIREMENT.md` or an acceptance
  criterion**: discard the question, the requirement already decided it.

Conversely, these are **not** valid defaults no matter how obvious the choice seems: a new
cross-team API/contract, a product-intent call (覆盖 vs 合并, 触发口径, 时间窗口), or a scope
boundary. If the requirement is silent or ambiguous on one of these, it is a question.

## Sensible Defaults

For minor implementation details or edge cases, make reasonable best-practice
assumptions, document them in `GRILL.md` under `### 默认假设与实现细节 (Sensible Defaults)`,
and do not ask them. Check every default against `REQUIREMENT.md` and the acceptance
criteria first: a default that **contradicts** the requirement is not a default — raise the
conflict as a question.

## Prove the frontier is empty (mandatory before `done: true`)

Enumerate the design tree's top-level branches — scope/permissions, data & 口径,
external/AI contract, UI & interaction, state & rollback, exceptions — and append a
`### Frontier 清点` block to `GRILL.md` marking each `已问` / `默认` / `不适用` with a one-line
reason. **Nothing may be left silently assumed.** This is an instruction, not a machine-checked
gate — nothing downstream validates the block, so a `done: true` without it silently degrades
the alignment rather than being rejected. Write it before declaring done.

## Steps

1. Read `REQUIREMENT.md`, source clones, and existing `GRILL.md` (previous answers stay).
2. If previous answers have settled the core flow/contracts, or if round count reaches the
   target (or hard cap of 3 rounds) without new P0 blockers, first append the
   `### Frontier 清点` block, then write `{"done": true, "round": N, "questions": []}` to
   `reqs/<JIRA>/.grill-round.json` and stop. Do not invent questions to fill the round.
3. Otherwise, append this round's `❓` / `➡️` questions to `reqs/<JIRA>/GRILL.md`.
4. Write `reqs/<JIRA>/.grill-round.json` with the schema below.
5. Stop. Do not invent user answers. Do not write SPEC.md or TICKETS.md.

If `GRILL.md` already has answers for the last round, recompute the **next** frontier from those answers.

When the frontier is empty, or when core architecture/business agreements are settled, append the `### Frontier 清点` block and write `{"done": true, "round": N, "questions": []}` and stop. Next command is `dev-yard spec <JIRA>` (the web UI offers Spec).

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
      "suggested_text": "the ➡️ recommendation, one or two sentences",
      "why_ask": "why this is a real P0/P1 blocker that research cannot settle",
      "evidence": "what docs/code you inspected and why they still leave it open"
    }
  ]
}
```

- Closed decision: 2–5 `options`. `suggested` is the recommended option id. `suggested_text` is the ➡️ sentence.
- Open decision: `options` is `[]`. Put the recommendation in `suggested_text`.
- `id` is `Q1`, `Q2`, … within this round. `title` is never empty.
- `why_ask` and `evidence` are **required**. The runner drops any question missing either,
  and if every question is dropped the round is treated as `done` — if that happens, still
  append the `### Frontier 清点` block so the skip is recorded rather than silent.
- `done: true` only after the `### Frontier 清点` block is written and nothing is left to ask.
