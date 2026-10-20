import { useEffect, useState } from "react";
import { apiErrorMessage, worldApi } from "../api/client";
import type { SavedInvocation } from "../types/world";

export function useSavedInvocations(runId: string | undefined, kind: "models" | "tools", active: boolean) {
  const [state, setState] = useState<{ runId?: string; kind?: string; rows: SavedInvocation[]; error?: string }>({ rows: [] });
  useEffect(() => {
    if (!runId) return;
    let current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function read() {
      try {
        const rows: SavedInvocation[] = [];
        let offset: number | null = 0;
        while (offset !== null && current) {
          const page = await worldApi.getRunInvocations(runId!, kind, offset);
          rows.push(...page.items);
          if (page.next_offset !== null && page.next_offset <= offset) throw new Error("Invalid invocation page");
          offset = page.next_offset;
        }
        if (current) setState({ runId, kind, rows });
      } catch (error) {
        if (current) setState(previous => ({ runId, kind,
          rows: previous.runId === runId && previous.kind === kind ? previous.rows : [], error: apiErrorMessage(error) }));
      } finally { if (current && active) timer = setTimeout(() => void read(), 2000); }
    }
    void read();
    return () => { current = false; clearTimeout(timer); };
  }, [runId, kind, active]);
  return state.runId === runId && state.kind === kind ? state : { rows: [] };
}
