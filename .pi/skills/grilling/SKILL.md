---
name: grilling
description: Stress-test and align on critical architecture and business decisions efficiently. Use when the user wants to stress-test their thinking, or uses any 'grill' trigger phrases.
---

Interview the user to reach a shared understanding on core architecture and business requirements. Map this as a **design tree**, actively converging toward resolution across **1–3 bounded rounds** instead of expanding into an endless loop.

## Core Rules

1. **Strict Total Rounds Cap (死守总轮次，严禁无限 Ping-Pong)**:
   - **Small tasks**: 1 round.
   - **Medium tasks**: 1–2 rounds.
   - **Large / Cross-repo / Epic tasks**: 2–3 rounds.
   - **Hard Cap**: Strictly capped at **3 rounds maximum**. Round 1 expands the initial frontier across the full scope; Round 2 converges on settled answers and derived blockers; Round 3 (if ever reached) seals final disputes, after which the session must close.
   - **The round/question numbers are a ceiling, not a quota.** Never pad a round to reach a size. Zero questions is legal, but it must be **earned** by Rule 7's frontier clean-up — never assumed because the requirement "looks clear".

2. **Adaptive Question Volume & Thematic Grouping (按规模弹性题量 + 模块化分组)**:
   - Ask as many questions as the requirement genuinely needs to cover P0/P1 blockers in **one go**, and no more. Do not manufacture questions to fill a round.
   - When a round contains multiple questions (≥ 4), **group them by module/theme** (e.g., `### 模块一：核心业务流程与状态机`, `### 模块二：跨系统数据交互与契约`, `### 模块三：权限与逆向回滚策略`).
   - Every question **must** include a recommended answer (`➡️`) with a clear rationale.

3. **P0/P1 Blockers Only (只问关键阻断性问题)**:
   - Ask only about true business ambiguities, high-level scope boundaries, domain model conflicts, irreversible architectural decisions, or critical trade-offs that require human consensus.
   - **Always a question, never a default** — even when a similar existing feature exists to copy (reuse settles *how* you build an implementation detail, never *what* a new contract or product rule is):
     - a **new external / cross-team contract** (AI platform function name, request/response schema, callback path, service ownership) that the requirement does **not** already fix — if the requirement already states it, it is a fact, not a question (Rule 6);
     - a **product-intent call** where the requirement is silent or the wording is ambiguous (e.g. 覆盖 vs 合并, 触发口径, 时间窗口);
     - a **scope boundary** (which entries/versions/records are in or out).
   - **DO NOT** ask about low-level implementation details, standard error codes, pagination defaults, naming trivia, or common edge cases with standard industry practices.

4. **Sensible Defaults (合理默认假设机制)**:
   - For all secondary **implementation** details and minor edge cases, **make reasonable best-practice default assumptions**.
   - Sensible Defaults are for *how to build*, not for *what to build*. Product intent, scope, and external/cross-team contracts never become defaults (Rule 3).
   - Document these in a dedicated section `### 默认假设与实现细节 (Sensible Defaults)` in the output artifact instead of questioning the user. Phrase each as a testable statement and cite the closest existing feature/file it derives from.
   - **Contradiction rule**: before writing a default, check it against `REQUIREMENT.md` and every acceptance criterion. A default that **contradicts** the requirement is **not a default** — raise the conflict as a question instead of silently picking a side.
   - "Which table/column/database?", "what error toast?", "which role?", "which page?"
     are almost always defaults, not questions — answer them from the closest existing
     feature the requirement points at.

5. **Facts are Your Job (自主查验事实)**:
   - Never ask the user for facts obtainable from the codebase, schemas, or existing requirements. Inspect them autonomously.
   - Concretely: if the answer is already in the requirement doc, in your own
     「关键事实（已自查）」 list, or in the source you just read, it is **not** a question.

6. **Known-Answer Questions Are Defects (明知故问即缺陷)**:
   - Before writing a question, run this gate; it must pass **all four** or the item
     becomes a Sensible Default instead:
     1. Not already stated in the requirement or an acceptance criterion.
     2. Not already listed in your own「关键事实（已自查）」.
     3. Not derivable from the source code, schema, or existing requirements.
     4. Getting it wrong would cause rework, or it is irreversible / cross-team / a product-intent call. **A similar existing feature does not demote a cross-team or product-intent decision to a default** — see Rule 3.
   - If your **recommended answer contradicts the requirement or an acceptance
     criterion**, discard the question — the requirement already decided it. (The inverse
     also holds: a *default* that contradicts the requirement is not a default — see Rule 4.)
   - Record the researched answer in the Sensible Defaults / 关键事实 section rather than
     surfacing it as a choice.

7. **Clear Stopping Criteria (明确收敛停机)**:
   - When core workflows, domain models, and key contracts are settled, when the frontier
     is empty, or when the round budget is reached, **declare the frontier empty — do not
     invent filler questions**.
   - **Frontier clean-up is mandatory before stopping**: write a `### Frontier 清点` block in
     `GRILL.md` enumerating the design tree's top-level branches (scope/permissions, data
     & 口径, external/AI contract, UI & interaction, state & rollback, exceptions) and mark
     each `已问` / `默认` / `不适用` with a one-line reason. **Nothing may be left silently
     assumed.** This is an instruction, not a machine-checked gate — nothing downstream
     validates the block, so not writing it silently degrades the alignment. Write it before declaring `done`.
   - Summarize consensus and recorded sensible defaults, and guide the user to the next stage (`dev-yard spec`).

## Question Format

Each question in a round should be structured as:

```
❓ **Q1** - **<question title>**: <concise question body explaining the trade-off, context, and choices>

➡️ <your recommended answer with brief rationale>
```


