---
name: grilling
description: Stress-test and align on critical architecture and business decisions efficiently. Use when the user wants to stress-test their thinking, or uses any 'grill' trigger phrases.
---

Interview the user to reach a shared understanding on core architecture and business requirements. Map this as a **design tree**, actively converging toward resolution across **1–3 bounded rounds** instead of expanding into an endless loop.

## Core Rules

1. **Strict Total Rounds Cap (死守总轮次，严禁无限 Ping-Pong)**:
   - **Small tasks**: 1 round (1–3 questions).
   - **Medium tasks**: 1–2 rounds (3–5 questions/round).
   - **Large / Cross-repo / Epic tasks**: 2–3 rounds (5–8+ questions/round).
   - **Hard Cap**: Strictly capped at **3 rounds maximum**. Round 1 expands the initial frontier across the full scope; Round 2 converges on settled answers and derived blockers; Round 3 (if ever reached) seals final disputes, after which the session must close.

2. **Adaptive Question Volume & Thematic Grouping (按规模弹性题量 + 模块化分组)**:
   - Scale question count adaptively based on requirement complexity: ask as many questions as needed to cover P0/P1 blockers in **one go**, rather than trickling them out over dozens of rounds.
   - When a round contains multiple questions (≥ 4), **group them by module/theme** (e.g., `### 模块一：核心业务流程与状态机`, `### 模块二：跨系统数据交互与契约`, `### 模块三：权限与逆向回滚策略`).
   - Every question **must** include a recommended answer (`➡️`) with a clear rationale.

3. **P0/P1 Blockers Only (只问关键阻断性问题)**:
   - Ask only about true business ambiguities, high-level scope boundaries, domain model conflicts, irreversible architectural decisions, or critical trade-offs that require human consensus.
   - **DO NOT** ask about low-level implementation details, standard error codes, pagination defaults, naming trivia, or common edge cases with standard industry practices.

4. **Sensible Defaults (合理默认假设机制)**:
   - For all secondary implementation details and minor edge cases, **make reasonable best-practice default assumptions**.
   - Document these in a dedicated section `### 默认假设与实现细节 (Sensible Defaults)` in the output artifact instead of questioning the user.

5. **Facts are Your Job (自主查验事实)**:
   - Never ask the user for facts obtainable from the codebase, schemas, or existing requirements. Inspect them autonomously.

6. **Clear Stopping Criteria (明确收敛停机)**:
   - When core workflows, domain models, and key contracts are settled (or after reaching the round budget), declare the frontier empty.
   - Summarize consensus and recorded sensible defaults, and guide the user to the next stage (`dev-yard spec`).

## Question Format

Each question in a round should be structured as:

```
❓ **Q1** - **<question title>**: <concise question body explaining the trade-off, context, and choices>

➡️ <your recommended answer with brief rationale>
```


