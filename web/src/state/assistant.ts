import { ref } from "vue";
import type { SuggestedAction } from "@/api/types";

export const assistantOpen = ref(false);

export function openAssistant() {
  assistantOpen.value = true;
}

export function closeAssistant() {
  assistantOpen.value = false;
}

export interface ActionRequest {
  action: string;
  jira: string;
  act: SuggestedAction;
}

/** Board actions that open a confirm form; they must not start a job. */
export const INTERACTIVE_ACTIONS = new Set([
  "change",
  "qa-review",
  "fill-test-report",
]);

type ActionHandler = (req: ActionRequest) => boolean | Promise<boolean>;

const actionHandlers = new Set<ActionHandler>();

export function registerActionHandler(handler: ActionHandler): () => void {
  actionHandlers.add(handler);
  return () => {
    actionHandlers.delete(handler);
  };
}

export async function dispatchActionRequest(req: ActionRequest): Promise<boolean> {
  for (const handler of actionHandlers) {
    const handled = await handler(req);
    if (handled) return true;
  }
  return false;
}
