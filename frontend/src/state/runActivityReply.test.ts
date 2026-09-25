import { describe, expect, it } from "vitest";
import { withoutFinalReply, type RunActivityState } from "./runActivity";

const state = (items: RunActivityState["items"]): RunActivityState => ({ items, seen: ["event-1"], truncated: false });
const reply = (id: string, text: string): RunActivityState["items"][number] => ({ id, type: "agent_message", text });

describe("Completed reply presentation", () => {
  it("removes only the final assistant row, keeping intermediate text and tools in order", () => {
    const source = state([
      { id: "plan", type: "agent_progress", text: "Checking" },
      reply("commentary", "A useful explanation"),
      { id: "tool", type: "tool_completed", name: "read_file", response: "Final answer" },
      reply("answer", "Final answer"),
    ]);
    expect(withoutFinalReply(source, "Final answer")?.items.map(item => item.id)).toEqual(["plan", "commentary", "tool"]);
  });

  it("preserves an earlier message with identical content", () => {
    const source = state([reply("earlier", "Done"), reply("answer", "Done")]);
    expect(withoutFinalReply(source, "Done")?.items.map(item => item.id)).toEqual(["earlier"]);
  });

  it("does not remove an earlier match when the last assistant row is different", () => {
    const source = state([reply("earlier", "Done"), reply("later", "Still checking")]);
    expect(withoutFinalReply(source, "Done")).toBe(source);
  });

  it("allows trailing tool or progress events without dropping them", () => {
    const source = state([reply("answer", "Done"),
      { id: "tool", type: "tool_completed", name: "read_file", response: "Done" },
      { id: "progress", type: "agent_progress", text: "Done" }]);
    expect(withoutFinalReply(source, "Done")?.items.map(item => item.id)).toEqual(["tool", "progress"]);
  });

  it("ignores only outer whitespace, without fuzzy or substring matching", () => {
    const source = state([reply("answer", "  **Done**\n")]);
    expect(withoutFinalReply(source, "**Done**")?.items).toEqual([]);
    expect(withoutFinalReply(source, "**Done** with more information")).toBe(source);
  });

  it("does not filter a live or failed run without a durable final reply", () => {
    const source = state([reply("partial", "Work in progress")]);
    expect(withoutFinalReply(source)).toBe(source);
    expect(withoutFinalReply(source, " \n")).toBe(source);
    expect(withoutFinalReply(undefined, "Done")).toBeUndefined();
  });

  it("leaves a tool-only restored trace unchanged", () => {
    const source = state([{ id: "tool", type: "tool_completed", name: "command", response: "Done" }]);
    expect(withoutFinalReply(source, "Done")).toBe(source);
  });

  it("does not mutate cached activity or change retained row identities", () => {
    const source = state([reply("commentary", "Inspecting"), reply("answer", "Done")]);
    source.items.forEach(Object.freeze);
    Object.freeze(source.items);
    Object.freeze(source);
    const result = withoutFinalReply(source, "Done")!;
    expect(source.items).toHaveLength(2);
    expect(result.items[0]).toBe(source.items[0]);
    expect(result.seen).toBe(source.seen);
    expect(result.truncated).toBe(source.truncated);
    expect(withoutFinalReply(source, "Done")).toEqual(result);
  });
});
