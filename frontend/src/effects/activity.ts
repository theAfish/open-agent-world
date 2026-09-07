import type { RuntimeEvent, WorldCard } from "../types/world";
import { ownedDescendants } from "../state/containers";

export type ActivityPhase = "idle" | "running" | "waiting" | "completed" | "stopped" | "failed";
export interface NodeActivity { phase: ActivityPhase; running: number; waiting: number }

/** Operational status wins while busy; terminal Run events describe the outcome. */
export function nodeActivity(card: WorldCard, cards: WorldCard[], events: RuntimeEvent[], aggregate = false): NodeActivity {
  const members = aggregate ? [card, ...ownedDescendants(cards, card.id)] : [card];
  const running = members.filter((node) => node.status === "running").length;
  const waiting = members.filter((node) => ["waiting", "paused"].includes(node.status)).length;
  if (running || waiting) return { phase: running ? "running" : "waiting", running, waiting };
  const phases = members.map((node): ActivityPhase => {
    if (node.status === "error") return "failed";
    const latest = events.find((event) => (event.node_id ?? event.agent_id) === node.id && event.type.startsWith("run_"));
    switch (latest?.type) {
      case "run_failed": return "failed";
      case "run_succeeded": return "completed";
      case "run_cancelled": case "run_interrupted": return "stopped";
      default: return "idle";
    }
  });
  return { phase: phases.includes("failed") ? "failed" : phases.includes("stopped") ? "stopped"
    : phases.includes("completed") ? "completed" : "idle", running, waiting };
}
