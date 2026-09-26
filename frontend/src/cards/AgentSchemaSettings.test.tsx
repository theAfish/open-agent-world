// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import type { NodeTypeCatalogItem, WorldCard } from "../types/world";
import { AgentSchemaSettings } from "./AgentSchemaSettings";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("edits plugin-defined settings and shows detected runtime without exposing constant fields", async () => {
  const card: WorldCard = { id: "custom", type: "vendor.worker", name: "Worker", position: { x: 0, y: 0 },
    size: { width: 300, height: 190 }, expanded: false, status: "idle", config: { model: "default", effort: "default" } };
  const definition = { id: card.type, config_schema: { properties: {
    model: { type: "string", title: "Worker model" },
    effort: { type: "string", title: "Reasoning effort", enum: ["default", "high"] },
    runtime_provider_id: { type: "string", title: "Fixed provider", const: "vendor.runtime" },
  } } } as unknown as NodeTypeCatalogItem;
  const update = vi.fn().mockResolvedValue(undefined);
  useWorldStore.setState({ catalog: { packs: [], plugins: [], node_types: [definition], relationships: [] }, updateCard: update });
  vi.spyOn(worldApi, "getAgentInfo").mockResolvedValue({ session_id: "thread-123", details: { source: "desktop", version: "codex-cli 0.153.4" } });
  render(<AgentSchemaSettings card={card} />);
  expect(await screen.findByText("codex-cli 0.153.4")).toBeTruthy();
  expect(screen.queryByLabelText("Fixed provider")).toBeNull();
  fireEvent.change(screen.getByLabelText("Reasoning effort"), { target: { value: "high" } });
  await waitFor(() => expect(update).toHaveBeenCalledWith("custom", { config: { effort: "high" } }));
  const model = screen.getByLabelText("Worker model");
  fireEvent.change(model, { target: { value: "chosen-model" } });
  fireEvent.blur(model);
  await waitFor(() => expect(update).toHaveBeenCalledWith("custom", { config: { model: "chosen-model" } }));
});

it('pauses runtime discovery for a retained hidden inspector', async () => {
  const card: WorldCard = { id: 'hidden-agent', type: 'agent', name: 'Agent', position: { x: 0, y: 0 },
    size: { width: 300, height: 190 }, expanded: false, status: 'idle', config: {} };
  const get = vi.spyOn(worldApi, 'getAgentInfo').mockResolvedValue({ session_id: 'runtime', details: {} });
  const view = render(<AgentSchemaSettings card={card} active={false} />);
  expect(get).not.toHaveBeenCalled();
  view.rerender(<AgentSchemaSettings card={card} active />);
  await waitFor(() => expect(get).toHaveBeenCalledTimes(1));
  view.rerender(<AgentSchemaSettings card={{ ...card, status: 'running', config: { changed: true } }} active={false} />);
  expect(get).toHaveBeenCalledTimes(1);
});
