import { describe, expect, it } from "vitest";
import { buildCardDraft } from "./helpers";
import { appendMention, resolveConversationTargets } from "./conversationMentions";
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

  it("inserts a readable mention token", () => {
    expect(appendMention("Please ask", "River Stone")).toBe("Please ask @River Stone ");
  });
});
