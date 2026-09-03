import { describe, expect, it } from "vitest";
import { buildCardDraft } from "./helpers";
import {
  appendMention,
  completeMention,
  mentionCompletion,
  resolveConversationTargets,
} from "./conversationMentions";
import type { WorldCard } from "../types/world";

function agent(id: string, name: string): WorldCard {
  return { id, ...buildCardDraft("agent", { x: 0, y: 0 }), name };
}

describe("conversation mention routing", () => {
  const atlas = agent("atlas", "Atlas");
  const river = agent("river", "River Stone");

  it("resolves explicit names to stable agent ids", () => {
    expect(resolveConversationTargets("@Atlas review this with @River Stone!", [atlas, river]))
      .toEqual(["atlas", "river"]);
  });

  it("uses the selected participant only when no explicit mention exists", () => {
    expect(resolveConversationTargets("Please review this", [atlas, river], "river"))
      .toEqual(["river"]);
  });

  it("does not treat a partial name as an explicit mention", () => {
    expect(resolveConversationTargets("@Riverboat", [river, atlas])).toEqual([]);
  });

  it("does not fall back to the selected Agent when an unmatched @ is present", () => {
    expect(resolveConversationTargets("@Nobody review this", [river, atlas], "atlas"))
      .toEqual([]);
  });

  it("offers matching participants while a mention is being typed", () => {
    expect(mentionCompletion("Ask @ver", 8, [atlas, river])).toEqual({
      start: 4,
      end: 8,
      query: "ver",
      candidates: [river],
    });
    expect(mentionCompletion("Ask @missing", 12, [atlas, river])).toBeUndefined();
    expect(mentionCompletion("Tell (@Atl", 10, [atlas, river])?.candidates).toEqual([atlas]);
  });

  it("routes an explicit mention from the middle of a message", () => {
    expect(resolveConversationTargets("Could @Atlas check this?", [atlas, river]))
      .toEqual(["atlas"]);
  });

  it("routes and completes a mention immediately after text", () => {
    expect(resolveConversationTargets("请@Atlas 确认", [atlas, river]))
      .toEqual(["atlas"]);
    expect(mentionCompletion("请@Atl", 5, [atlas, river])?.candidates).toEqual([atlas]);
  });

  it("replaces the active token and returns the next caret position", () => {
    expect(completeMention("Ask @riv tomorrow", { start: 4, end: 8 }, "River Stone"))
      .toEqual({ content: "Ask @River Stone tomorrow", caret: 16 });
  });

  it("inserts a readable mention token", () => {
    expect(appendMention("Please ask", "River Stone")).toBe("Please ask @River Stone ");
  });
});
