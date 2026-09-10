// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup } from "@testing-library/react";
import { worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import { buildCardDraft } from "../state/helpers";
import { TextCardBody } from "./TextCard";

vi.mock("./CardUtilities", () => ({ RelationshipList: () => null }));
const card = { id: "minutes", ...buildCardDraft("text", { x: 0, y: 0 }), config: { content: "", revision: 2, history: [] } };
const originalSaveText = useWorldStore.getState().saveText;
beforeEach(() => vi.restoreAllMocks());
afterEach(() => { cleanup(); useWorldStore.setState({ saveText: originalSaveText }); });

it("loads real minutes and preserves them when a summary snapshot contains empty content", async () => {
  const get = vi.spyOn(worldApi, "getText").mockResolvedValue({ content: "Saved minutes", revision: 2, history: [] });
  const view = render(<TextCardBody card={card} level="inspector" />);
  await screen.findByDisplayValue("Saved minutes");
  view.rerender(<TextCardBody card={{ ...card, config: { ...card.config, preview: "Saved minutes" } }} level="inspector" />);
  expect(screen.getByRole("textbox")).toHaveValue("Saved minutes");
  get.mockResolvedValue({ content: "Updated by agent", revision: 3, history: [] });
  view.rerender(<TextCardBody card={{ ...card, config: { ...card.config, revision: 3 } }} level="inspector" />);
  await screen.findByDisplayValue("Updated by agent");
});

it("preserves a dirty draft and saves against its original revision after an agent update", async () => {
  const get = vi.spyOn(worldApi, "getText").mockResolvedValue({ content: "Saved minutes", revision: 2, history: [] });
  const save = vi.fn().mockResolvedValue(false);
  useWorldStore.setState({ saveText: save });
  const view = render(<TextCardBody card={card} level="inspector" />);
  await screen.findByDisplayValue("Saved minutes");
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "My draft" } });
  get.mockResolvedValue({ content: "Agent update", revision: 3, history: [] });
  view.rerender(<TextCardBody card={{ ...card, config: { ...card.config, revision: 3 } }} level="inspector" />);
  await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
  expect(screen.getByRole("textbox")).toHaveValue("My draft");
  fireEvent.click(screen.getByRole("button", { name: "Save text" }));
  await waitFor(() => expect(save).toHaveBeenCalledWith("minutes", "My draft", 2));
  expect(screen.getByRole("textbox")).toHaveValue("My draft");
});

it("does not permit saving a placeholder before the resource has loaded", () => {
  vi.spyOn(worldApi, "getText").mockReturnValue(new Promise(() => {}));
  render(<TextCardBody card={card} level="inspector" />);
  expect(screen.getByRole("textbox")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Save text" })).toBeDisabled();
});
