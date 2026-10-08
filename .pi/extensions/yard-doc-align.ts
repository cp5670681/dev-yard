/**
 * doc-align may write only its proposal JSON. pi's write tool is otherwise
 * unscoped, and a prose instruction does not stop a write into qa/ or a clone.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { isToolCallEventType } from "@earendil-works/pi-coding-agent";

const REASON =
  "dev-yard doc-align: 只能写提案 JSON（.doc-align/<ticket>.json），不能改文档、用例或代码。";

/** True when a write/edit path is the proposal file and does not escape it. */
export function isProposalWrite(path: string): boolean {
  const text = path.replaceAll("\\", "/").trim();
  if (!text || text.includes("..")) return false;
  return /(?:^|\/)\.doc-align\/[^/]+\.json$/.test(text);
}

export default function yardDocAlign(pi: ExtensionAPI): void {
  pi.on("tool_call", (event) => {
    if (!isToolCallEventType("write", event) && !isToolCallEventType("edit", event)) {
      return;
    }
    const target = event.input.path ?? "";
    if (!isProposalWrite(target)) {
      return { block: true, reason: `${REASON}（命中：${target || "(empty)"}）` };
    }
  });
}
