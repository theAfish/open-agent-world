import { afterEach, describe, expect, it, vi } from "vitest";
import { nodeActivity } from "./activity";
import { flightPosition, useGenerationStore } from "./generation";
import { buildCardDraft } from "../state/helpers";
import type { RuntimeEvent, WorldCard } from "../types/world";

const card = (id: string, status = "idle", parent_id?: string): WorldCard => ({
  id, ...buildCardDraft("agent", { x: 0, y: 0 }), status, parent_id,
});
const event = (node_id: string, type: string): RuntimeEvent => ({ id: type, type, node_id, timestamp: new Date().toISOString(), payload: {} });

afterEach(() => vi.useRealTimers());
describe("reusable node effects", () => {
  it("aggregates nested work and keeps active work ahead of an earlier failure", () => {
    const workspace = card("workspace"), nested = card("nested", "idle", workspace.id);
    const first = card("first", "running", nested.id), second = card("second", "waiting", workspace.id);
    const cards = [workspace, nested, first, second];
    expect(nodeActivity(workspace, cards, [event(first.id, "run_failed")], true)).toEqual({ phase: "running", running: 1, waiting: 1 });
    first.status = "idle";
    expect(nodeActivity(workspace, cards, [], true).phase).toBe("waiting");
    second.status = "idle";
    expect(nodeActivity(workspace, cards, [event(first.id, "run_failed")], true).phase).toBe("failed");
    expect(nodeActivity(workspace, cards, [event(first.id, "run_succeeded"), event(first.id, "run_failed")], true).phase).toBe("completed");
    expect(nodeActivity(workspace, cards, [event(first.id, "run_cancelled")], true).phase).toBe("stopped");
  });
  it("starts and lands exactly at its endpoints without mutating coordinates", () => {
    const from = { x: 25, y: 100 }, to = { x: 525, y: 300 };
    expect(flightPosition(from, to, 0)).toEqual(from);
    expect(flightPosition(from, to, 1)).toEqual(to);
    expect(flightPosition(from, to, 0.3).y).toBeLessThan(300);
    expect(from).toEqual({ x: 25, y: 100 });
  });
  it("deduplicates live generation events and expires unmounted destinations", () => {
    vi.useFakeTimers();
    useGenerationStore.setState({ items: [], seen: [] });
    const item = { id: "birth", sourceId: "template", targetId: "instance" };
    useGenerationStore.getState().enqueue(item);
    useGenerationStore.getState().enqueue(item);
    expect(useGenerationStore.getState().items).toHaveLength(1);
    vi.advanceTimersByTime(2400);
    expect(useGenerationStore.getState().items).toHaveLength(0);
    useGenerationStore.getState().enqueue(item);
    expect(useGenerationStore.getState().items).toHaveLength(0);
  });
});
