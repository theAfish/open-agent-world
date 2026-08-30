import { describe, expect, it } from "vitest";
import { normalizeCard, normalizeRuntimeEvent } from "./client";

describe("API normalization boundary", () => {
  it("merges authoritative resource metadata and status into a card", () => {
    const card = normalizeCard({
      id: "image-1",
      type: "image",
      name: "Plate",
      position: { x: 1, y: 2 },
      size: { width: 240, height: 220 },
      expanded: false,
      status: "available",
      config: { filename: "old.png" },
      resource: {
        filename: "plate.png",
        media_type: "image/png",
        size_bytes: 42,
        width: 7,
        height: 5,
        revision: 1,
      },
    });
    expect(card.config.filename).toBe("plate.png");
    expect(card.config.image_width).toBe(7);
    expect(card.config.image_height).toBe(5);
    expect(card.config.bytes).toBe(42);
    expect(card.config.preview_url).toContain("/resources/image-1/content");
  });

  it("normalizes operational events without inventing reasoning fields", () => {
    const event = normalizeRuntimeEvent({
      id: "evt",
      type: "stdout",
      sandbox_id: "lab",
      timestamp: "2026-01-01T00:00:00Z",
      payload: { text: "ok" },
    });
    expect(event.type).toBe("stdout");
    expect(event.sandbox_id).toBe("lab");
    expect(event.payload).toEqual({ text: "ok" });
  });
});
