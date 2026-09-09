import { inject, provide, reactive, type InjectionKey } from "vue";

export type SnackKind = "success" | "error" | "info";

export interface SnackState {
  show: boolean;
  text: string;
  color: SnackKind;
  notify: (text: string, color?: SnackKind) => void;
}

const KEY: InjectionKey<SnackState> = Symbol("snack");

export function provideSnack(): SnackState {
  const state = reactive<SnackState>({
    show: false,
    text: "",
    color: "info",
    notify(msg: string, kind: SnackKind = "info") {
      state.text = msg;
      state.color = kind;
      state.show = true;
    },
  });
  provide(KEY, state);
  return state;
}

export function useSnack() {
  const state = inject(KEY);
  if (!state) {
    return {
      notify: (_text: string, _color?: SnackKind) => undefined,
    };
  }
  return state;
}
