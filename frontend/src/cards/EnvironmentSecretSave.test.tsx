// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { worldApi } from "../api/client";
import { buildCardDraft } from "../state/helpers";
import { useNodeSurfaceStore } from "../state/nodeSurfaces";
import { useWorldStore } from "../state/worldStore";
import { ExecutionConfigurationBody } from "./ExecutionConfiguration";
import { SandboxEnvironment } from "./SandboxEnvironment";

describe.each(["sandbox", "environment"])("%s secret save", (type) => {
  const card = { id: `secret-${type}`, ...buildCardDraft(type, { x: 0, y: 0 }) };
  const saveLabel = type === "sandbox" ? "Save environment" : "Save";
  beforeEach(() => {
    vi.restoreAllMocks();
    useNodeSurfaceStore.setState({ drafts: {} });
    const catalog = useWorldStore.getState().catalog;
    useWorldStore.setState({ cards: [card], edges: [], events: [], catalog: { ...catalog,
      node_types: [...catalog.node_types.filter(n => n.id !== "environment"),
        { ...catalog.node_types[0], id: "environment", traits: ["core.environment"] }],
    } });
    vi.spyOn(worldApi, "getNodeDocument").mockResolvedValue({ value: { variables: {} }, revision: 3, summary: {} });
    vi.spyOn(worldApi, "getCredentialBindings").mockResolvedValue({});
    vi.spyOn(worldApi, "sandboxWorkspace").mockResolvedValue({ profile_id: null, ready: true, variables: [] });
    vi.spyOn(worldApi, "saveEnvironment").mockImplementation(async (_id, value) => ({ value, revision: 4, summary: {} }));
  });
  afterEach(cleanup);

  async function openEditor() {
    const result = render(type === "sandbox" ? <SandboxEnvironment card={card} /> : <ExecutionConfigurationBody card={card} />);
    if (type === "sandbox") fireEvent.click(screen.getByText("Environment variables"));
    await waitFor(() => expect((screen.getByRole("button", { name: saveLabel }) as HTMLButtonElement).disabled).toBe(false));
    return result;
  }

  it("saves a directly entered secret once without exposing it in the document or shared drafts", async () => {
    await openEditor();
    fireEvent.click(screen.getByRole("button", { name: "Add variable" }));
    fireEvent.change(screen.getByLabelText("Environment variable 1 name"), { target: { value: "API_TOKEN" } });
    fireEvent.change(screen.getByLabelText("Environment variable 1 type"), { target: { value: "secret" } });
    fireEvent.change(screen.getByLabelText("Environment variable 1 value"), { target: { value: "test-secret-742" } });
    expect(JSON.stringify(useNodeSurfaceStore.getState().drafts)).not.toContain("test-secret-742");
    fireEvent.click(screen.getByRole("button", { name: saveLabel }));
    await waitFor(() => expect(worldApi.saveEnvironment).toHaveBeenCalledTimes(1));
    const [id, value, secrets, revision] = vi.mocked(worldApi.saveEnvironment).mock.calls[0];
    const reference = (value.variables as Record<string, { secret_ref: string }>).API_TOKEN.secret_ref;
    expect(reference).not.toBe("test-secret-742");
    expect(JSON.stringify(value)).not.toContain("test-secret-742");
    expect([id, secrets, revision]).toEqual([card.id, { [reference]: "test-secret-742" }, 3]);
    await waitFor(() => expect((screen.getByLabelText("Environment variable 1 value") as HTMLInputElement).value).toBe(""));
    expect(screen.queryByRole("button", { name: "Bind secret" })).toBeNull();
  });

  it("keeps configured secrets when blank and accepts an inline replacement", async () => {
    vi.mocked(worldApi.getNodeDocument).mockResolvedValue({ value: { variables: { API_TOKEN: { secret_ref: "existing" } } }, revision: 3, summary: {} });
    vi.mocked(worldApi.getCredentialBindings).mockResolvedValue({ existing: true });
    await openEditor();
    await screen.findByText("Configured");
    fireEvent.click(screen.getByRole("button", { name: saveLabel }));
    await waitFor(() => expect(worldApi.saveEnvironment).toHaveBeenCalledWith(card.id, { variables: { API_TOKEN: { secret_ref: "existing" } } }, {}, 3));
    await waitFor(() => expect((screen.getByRole("button", { name: saveLabel }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.change(screen.getByLabelText("Environment variable 1 value"), { target: { value: "replacement" } });
    fireEvent.click(screen.getByRole("button", { name: saveLabel }));
    await waitFor(() => expect(worldApi.saveEnvironment).toHaveBeenLastCalledWith(card.id, { variables: { API_TOKEN: { secret_ref: "existing" } } }, { existing: "replacement" }, 4));
  });

  it("identifies an unfilled secret by variable name before submitting", async () => {
    vi.mocked(worldApi.getNodeDocument).mockResolvedValue({ value: { variables: { API_TOKEN: { secret_ref: "imported" } } }, revision: 3, summary: {} });
    await openEditor();
    fireEvent.click(screen.getByRole("button", { name: saveLabel }));
    await screen.findByText("Enter a secret for API_TOKEN, or remove the unused variable.");
    expect(worldApi.saveEnvironment).not.toHaveBeenCalled();
  });

  it.each(["folder", "file"] as const)("loads and saves a host %s through the shared editor without putting authority in the document", async (kind) => {
    const bindingKind = kind === "file" ? { kind } : {};
    vi.mocked(worldApi.getNodeDocument).mockResolvedValue({ value: { variables: { MODELS: { [`${kind}_ref`]: "models" } } }, revision: 3, summary: {} });
    vi.spyOn(worldApi, "getEnvironmentFolderBindings").mockResolvedValue({ models: { path: "D:\\Models", access: "read_only", ...bindingKind } });
    vi.spyOn(worldApi, "inspectEnvironmentPath").mockResolvedValue({ path: "D:\\Models", kind });
    await openEditor();
    expect((screen.getByLabelText("Environment variable 1 path") as HTMLInputElement).value).toBe("D:\\Models");
    fireEvent.change(screen.getByLabelText("Environment variable 1 access"), { target: { value: "read_write" } });
    fireEvent.click(screen.getByRole("button", { name: saveLabel }));
    await waitFor(() => expect(worldApi.saveEnvironment).toHaveBeenCalledWith(card.id,
      { variables: { MODELS: { path_ref: "models" } } }, {}, 3,
      { models: { path: "D:\\Models", access: "read_write" } }));
    await waitFor(() => expect((screen.getByRole("button", { name: saveLabel }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.change(screen.getByLabelText("Environment variable 1 type"), { target: { value: "value" } });
    expect(screen.queryByLabelText("Environment variable 1 path")).toBeNull();
    fireEvent.change(screen.getByLabelText("Environment variable 1 value"), { target: { value: "plain" } });
    fireEvent.click(screen.getByRole("button", { name: saveLabel }));
    await waitFor(() => expect(worldApi.saveEnvironment).toHaveBeenLastCalledWith(card.id,
      { variables: { MODELS: "plain" } }, {}, 4));
  });

  it("ignores a late path inspection after the user changes the input", async () => {
    vi.mocked(worldApi.getNodeDocument).mockResolvedValue({ value: { variables: { DATA: { path_ref: "data" } } }, revision: 3, summary: {} });
    vi.spyOn(worldApi, "getEnvironmentFolderBindings").mockResolvedValue({ data: { path: "/old/model.pt", access: "read_only", kind: "file" } });
    let finish!: (result: { path: string; kind: "file" | "folder" }) => void;
    const inspect = vi.spyOn(worldApi, "inspectEnvironmentPath")
      .mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
      .mockResolvedValue({ path: "/new/folder", kind: "folder" });
    await openEditor();
    await waitFor(() => expect(inspect).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByLabelText("Environment variable 1 path"), { target: { value: "/new/folder" } });
    fireEvent.change(screen.getByLabelText("Environment variable 1 access"), { target: { value: "read_write" } });
    await screen.findByText(/Commands can create, change and delete files in this folder/);
    finish({ path: "/old/model.pt", kind: "file" });
    await waitFor(() => expect(screen.queryByText(/Commands can read and change this file/)).toBeNull());
    expect((screen.getByLabelText("Environment variable 1 path") as HTMLInputElement).value).toBe("/new/folder");
  });
});
