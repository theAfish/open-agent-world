import { useEffect, useState } from "react";
import { worldApi, apiErrorMessage } from "../api/client";
import type { SavedModelThinking } from "../types/world";

/** Read the same native Run archive for Agent and Conversation views. */
export function useSavedThinking(runId: string | undefined, active: boolean) {
  const [state, setState] = useState<{ runId?: string; rows: SavedModelThinking[]; error?: string }>({ rows: [] });
  useEffect(() => {
    if (!runId) return;
    let current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function read() {
      try {
        const rows: SavedModelThinking[] = [];
        let offset: number | null = 0;
        while (offset !== null && current) {
          const page = await worldApi.getRunReasoning(runId!, offset);
          rows.push(...page.items);
          if (page.next_offset !== null && page.next_offset <= offset) throw new Error("Invalid thinking page");
          offset = page.next_offset;
        }
        if (current) setState({ runId, rows });
      } catch (error) {
        if (current) setState(previous => ({ runId, rows: previous.runId === runId ? previous.rows : [], error: apiErrorMessage(error) }));
      } finally {
        if (current && active) timer = setTimeout(() => void read(), 2000);
      }
    }
    void read();
    return () => { current = false; clearTimeout(timer); };
  }, [runId, active]);
  return state.runId === runId ? state : { rows: [] };
}
