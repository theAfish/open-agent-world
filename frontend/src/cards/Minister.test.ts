import { describe, expect, it } from "vitest";
import { ministerToolSummary } from "./Minister";

describe("Minister activity language", () => {
  it("uses readable activity labels while keeping details behind the disclosure", () => {
    expect(ministerToolSummary("Using canvas_inspect", "tool_started")).toBe("Checking this area");
    expect(ministerToolSummary('Finished canvas_connect\n\n{"id":"edge-1"}', "tool_completed")).toBe("Cards connected");
  });
  it("does not label a rejected operation as completed", () => {
    expect(ministerToolSummary('Finished canvas_connect\n\n{"ok":false,"error":{"code":"permission_denied"}}', "tool_completed"))
      .toBe("Connecting cards: needs attention");
  });
  it("does not label a pending confirmation as an applied mutation", () => {
    expect(ministerToolSummary('Finished canvas_delete\n\n{"status":"confirmation_required"}', 'tool_completed'))
      .toBe('Waiting for your confirmation');
  });
});
