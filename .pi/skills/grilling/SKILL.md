---
name: grilling
description: Stress-test and align on critical architecture and business decisions efficiently. Use when the user wants to stress-test their thinking, or uses any 'grill' trigger phrases.
---

Interview the user to reach a shared understanding on core architecture and business requirements. Map this as a **design tree**, but **actively converge toward resolution instead of expanding infinitely into implementation minutiae**.

## Core Rules

1. **Strict Budget & Quick Convergence**:
   - Limit the grilling process to **1–2 rounds** (hard cap: max 3 rounds).
   - Ask at most **2–4 critical (P0/P1) questions per round**. Consolidate related questions.
2. **P0/P1 Blockers Only**:
   - Only ask questions that represent true business ambiguities, high-level scope boundaries, or major architectural trade-offs that cannot be decided automatically.
   - **DO NOT** question low-level implementation details, standard error handling, HTTP response formats, pagination defaults, or trivia that have obvious industry standard practices.
3. **Sensible Defaults (合理默认假设)**:
   - For secondary implementation choices or non-critical edge cases, **make a reasonable best-practice default assumption**.
   - Document these assumptions under a dedicated `### 默认假设与实现细节 (Sensible Defaults)` section in the artifact rather than bothering the user with more questions.
4. **Facts are Your Job**:
   - Finding _facts_ from the codebase, schemas, and existing files is your job, never the user's. Use tools/sub-agents to find them autonomously.
5. **Clear Stopping Criteria**:
   - Stop when the core flow, key domain models, and essential contracts are agreed upon, or when no blocking (P0/P1) ambiguities remain.
   - Do not wait for every theoretical edge case to be discussed.
   - When finished, summarize the consensus and sensible defaults, then prompt the user to proceed to the next stage.

## Question Format

Each question in a round should be structured as:

```
❓ **Q1** - **<question title>**: <concise question body explaining the trade-off and options>

➡️ <your recommended answer with brief rationale>
```

