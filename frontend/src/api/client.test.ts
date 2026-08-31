import { afterEach, describe, expect, it, vi } from "vitest";
import { normalizeCard, normalizeRuntimeEvent, worldApi } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

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

  it("uses the backend's media_type field when importing an image", async () => {
    class TestFileReader {
      result = "data:image/png;base64,cG5n";
      addEventListener(event: string, listener: () => void) {
        if (event === "load") queueMicrotask(listener);
      }
      readAsDataURL() {}
    }
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", {
      status: 201,
      headers: { "content-type": "application/json" },
    }));
    vi.stubGlobal("FileReader", TestFileReader);
    vi.stubGlobal("fetch", fetchMock);

    await worldApi.uploadImage("image 1", { name: "sample.png", type: "image/png" } as File);

    const [, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(options.body))).toMatchObject({
      filename: "sample.png",
      media_type: "image/png",
      data_base64: "cG5n",
    });
    expect(JSON.parse(String(options.body))).not.toHaveProperty("mime_type");
  });

  it("derives an accepted media type from the filename when the browser omits it", async () => {
    class TestFileReader {
      result = "data:application/octet-stream;base64,cG5n";
      addEventListener(event: string, listener: () => void) {
        if (event === "load") queueMicrotask(listener);
      }
      readAsDataURL() {}
    }
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", {
      status: 201,
      headers: { "content-type": "application/json" },
    }));
    vi.stubGlobal("FileReader", TestFileReader);
    vi.stubGlobal("fetch", fetchMock);

    await worldApi.uploadImage("image-1", { name: "sample.webp", type: "" } as File);

    const [, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(options.body))).toMatchObject({ media_type: "image/webp" });
  });

  it("surfaces backend validation messages instead of a generic status", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: [{ msg: "Field required" }],
    }), {
      status: 422,
      headers: { "content-type": "application/json" },
    })));

    await expect(worldApi.getWorld()).rejects.toMatchObject({
      message: "Field required",
      status: 422,
    });
  });
});
