import { useEffect, useState } from "react";
import { apiErrorMessage, worldApi } from "../api/client";
import type { SavedInvocation } from "../types/world";

export function useInvocationDetail(runId: string | undefined, kind: "models" | "tools" | "activity", identity: string | undefined,
                                    open: boolean, active: boolean) {
  const [state, setState] = useState<{ runId?: string; kind?: string; identity?: string; row?: Partial<SavedInvocation> & { text?: string }; error?: string }>({});
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!open || !runId || !identity) return;
    let current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function read() {
      try {
        const row = kind === 'activity' ? await worldApi.getRunActivityDetail(runId!, identity!)
          : await worldApi.getRunInvocation(runId!, kind, identity!);
        if (current) setState({ runId, kind, identity, row });
      } catch (error) {
        if (current) setState(previous => ({ runId, kind, identity,
          row: previous.runId === runId && previous.kind === kind && previous.identity === identity ? previous.row : undefined,
          error: apiErrorMessage(error) }));
      } finally { if (current && active) timer = setTimeout(() => void read(), 2000); }
    }
    void read();
    return () => { current = false; clearTimeout(timer); };
  }, [runId, kind, identity, open, active, attempt]);
  const matched = state.runId === runId && state.kind === kind && state.identity === identity;
  return { row: matched ? state.row : undefined, error: matched ? state.error : undefined,
           retry: () => setAttempt(value => value + 1) };
}
