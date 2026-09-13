// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useLocale } from "../i18n";
import DevelopmentPanel from "./DevelopmentPanel";
const mocks = vi.hoisted(() => ({ mode: "development", flush: vi.fn().mockResolvedValue(undefined) }));
vi.mock("../state/profileStorage", () => ({
  currentProfile: () => ({ mode: mocks.mode }), flushPreferences: mocks.flush,
  profileStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
}));
beforeEach(() => { mocks.mode = "development"; useLocale.getState().setLocale("en"); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

it("does not handle F3 or show controls on a production profile", () => {
  mocks.mode = "production";
  render(<DevelopmentPanel />);
  const event = new KeyboardEvent("keydown", { key: "F3", cancelable: true });
  window.dispatchEvent(event);
  expect(event.defaultPrevented).toBe(false);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByText("DEV · F3")).toBeNull();
});

it("requires reviewing a reset before submitting the exact profile and generation", async () => {
  const request = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({
    profile_id: "dev-test", generation: "generation-7", data_root: "D:/test/dev", profile: "test", scopes: ["workspace", "tutorial"], world_cards: 42,
  }))).mockResolvedValueOnce(new Response('{"status":"restarting"}', { status: 202 }));
  vi.stubGlobal("fetch", request);
  render(<DevelopmentPanel />);
  const event = new KeyboardEvent("keydown", { key: "F3", cancelable: true });
  window.dispatchEvent(event);
  await screen.findByRole("dialog");
  expect(event.defaultPrevented).toBe(true);
  expect(screen.queryByRole("button", { name: "Confirm and restart" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Review reset" }));
  await screen.findByText("Clear all 42 cards in the world.");
  expect(request).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Confirm and restart" }));
  await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
  expect(JSON.parse(request.mock.calls[1][1].body)).toEqual({ scopes: ["workspace"], profile_id: "dev-test", generation: "generation-7" });
  await screen.findByRole("status");
});

it("invalidates the confirmation when the scope changes", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ profile_id: "a", generation: "b", data_root: "dev", profile: "test", scopes: ["workspace"], world_cards: 1 }))));
  render(<DevelopmentPanel />);
  fireEvent.click(screen.getByText("DEV · F3"));
  fireEvent.click(screen.getByRole("button", { name: "Review reset" }));
  await screen.findByRole("button", { name: "Confirm and restart" });
  fireEvent.click(screen.getByRole("button", { name: "Test pack opening" }));
  expect(screen.queryByRole("button", { name: "Confirm and restart" })).toBeNull();
});
