/**
 * Review verdict tool. Loaded only for the review and contract stages.
 *
 * The written report stays prose. The pass/fail decision is this tool call,
 * which pi emits as its own JSON event. Yard reads that event and does not
 * scan the report text.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

/** Google-compatible string enum. pi aliases `typebox`, not the pi-ai helper path. */
function stringEnum(values: readonly string[], description: string) {
  return Type.Unsafe({ type: "string", enum: values, description });
}

const Finding = Type.Object({
  id: Type.String({ description: "Stable finding id, e.g. F1" }),
  title: Type.String({ description: "Short title" }),
  repo: Type.String({ description: "repos.yaml alias" }),
  detail: Type.String({ description: "What is missing or wrong" }),
  files: Type.Optional(Type.Array(Type.String({ description: "Repo-relative path" }))),
  depends_on: Type.Optional(Type.Array(Type.String({ description: "Other finding id" }))),
});

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: "submit_review",
    label: "Submit review",
    description:
      "Submit the review verdict. Call exactly once, after the written report. " +
      "This call is the only pass/fail signal; the report text is not scanned.",
    promptSnippet: "submit_review — record the verdict (passed or failed) separately from the report",
    promptGuidelines: [
      "Finish a review or contract review by calling submit_review exactly once.",
      "verdict is passed or failed. Do not put the verdict in the report text.",
      "Contract gaps go in findings on that call, one object per independent gap.",
    ],
    parameters: Type.Object({
      verdict: stringEnum(
        ["passed", "failed"],
        "passed when the review accepts the change, failed when it must be reworked",
      ),
      findings: Type.Optional(
        Type.Array(Finding, {
          description: "Contract gaps. Omit or pass an empty list for a ticket review.",
        }),
      ),
    }),
    async execute(_toolCallId, params) {
      if (params.verdict !== "passed" && params.verdict !== "failed") {
        return {
          content: [{ type: "text", text: "verdict must be passed or failed" }],
          details: {},
          isError: true,
        };
      }
      const findings = params.findings ?? [];
      return {
        content: [{ type: "text", text: `recorded ${params.verdict}` }],
        details: { verdict: params.verdict, findings },
      };
    },
  });
}
