// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PaperFigureModeling } from "../../../plugins/library/frontend/PaperFigureModeling";
import { worldApi } from "../api/client";
import { useLocale } from "../i18n";
import { useAutoResearch } from "../state/autoResearch";
import { useNodeSurfaceStore } from "../state/nodeSurfaces";
import { useWorldStore } from "../state/worldStore";

const refreshWorld = vi.fn().mockResolvedValue(undefined);
const selectCards = vi.fn();
const annotation = { id: "figure-1", page: 3, text: "", comment: "", translation: "", rects: [[.1, .2, .6, .4]], image: "data:image/png;base64,cHJldmlldw==", document_version_id: "pdf-current" };
const props = { paperId: "paper-1", documentVersionId: "pdf-current", annotation, saved: true };
const originalWorld = useWorldStore.getState();

beforeEach(() => {
  vi.restoreAllMocks(); refreshWorld.mockClear(); selectCards.mockClear();
  useLocale.setState({ locale: "zh-CN" });
  useAutoResearch.setState({ scopeId: undefined });
  useWorldStore.setState({ cards: [], refreshWorld, selectCards, modelCatalog: {
    revision: 2, default_model: "oaw:model:text", connections: [{
      id: "active", name: "Local", adapter: "openai", base_url: "http://localhost:8000", auth_mode: "none", api_key_configured: false, enabled: true,
      models: [{ id: "text", name: "Text only", model_id: "text", enabled: true },
        { id: "vision", name: "Vision model", model_id: "vision", enabled: true, supports_images: true },
        { id: "disabled", name: "Disabled model", model_id: "disabled", enabled: false, supports_images: true }],
    }, { id: "off", name: "Disabled connection", adapter: "openai", base_url: "", auth_mode: "none", api_key_configured: false, enabled: false,
      models: [{ id: "off-vision", name: "Off vision", model_id: "vision", enabled: true, supports_images: true }],
    }],
  } });
  vi.spyOn(worldApi, "getNodeDocumentSummary").mockResolvedValue({ revision: 12, summary: {} });
  vi.spyOn(worldApi, "runAgent").mockResolvedValue({ accepted: true, run_id: "unexpected" });
  vi.spyOn(useNodeSurfaceStore.getState(), "openWorkspace").mockImplementation(() => {});
  vi.spyOn(useNodeSurfaceStore.getState(), "openInspector").mockImplementation(() => {});
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); useWorldStore.setState(originalWorld); });

function mount(overrides = {}) {
  const view = render(<PaperFigureModeling {...props} {...overrides}/>);
  fireEvent.click(screen.getByRole("button", { name: "用此图建模" }));
  return view;
}

it("only offers enabled models with explicit image support", () => {
  mount();
  const models = screen.getByRole("combobox", { name: "建模模型" }) as HTMLSelectElement;
  expect([...models.options].map(option => option.value)).toEqual(["oaw:model:vision"]);
  expect(models.value).toBe("oaw:model:vision");
});

it("sends saved source identity and fresh revision, then opens both cards without running the Agent", async () => {
  useAutoResearch.setState({ scopeId: "unrelated-scope" });
  const result = { paper_id: "paper-1", image_id: "image-1", agent_id: "agent-1", structure_id: "structure-1", model: "oaw:model:vision", replay: false, run_prompt: "Observe the source and construct a labeled draft." };
  const request = vi.fn().mockResolvedValue(new Response(JSON.stringify(result), { status: 200 }));
  vi.stubGlobal("fetch", request);
  mount();
  fireEvent.click(screen.getByRole("button", { name: "创建建模工作区" }));
  await screen.findByRole("status");
  expect(worldApi.getNodeDocumentSummary).toHaveBeenCalledWith("paper-1");
  expect(request.mock.calls[0][0]).toBe("/api/literature/papers/paper-1/model_figure");
  expect(JSON.parse(request.mock.calls[0][1].body)).toEqual({ expected_revision: 12, arguments: {
    annotation_id: "figure-1", document_version_id: "pdf-current", model: "oaw:model:vision",
  } });
  await waitFor(() => expect(useNodeSurfaceStore.getState().openWorkspace).toHaveBeenCalledWith("structure-1"));
  expect(useNodeSurfaceStore.getState().openInspector).toHaveBeenCalledWith("agent-1");
  expect(refreshWorld).toHaveBeenCalledOnce();
  expect(worldApi.runAgent).not.toHaveBeenCalled();
  expect((screen.getByRole("textbox", { name: "建模任务" }) as HTMLTextAreaElement).value).toBe(result.run_prompt);
});

it("waits for a saved crop and refuses a stale PDF version", () => {
  const request = vi.fn(); vi.stubGlobal("fetch", request);
  const view = mount({ saved: false });
  expect((screen.getByRole("button", { name: "等待截图保存…" }) as HTMLButtonElement).disabled).toBe(true);
  view.rerender(<PaperFigureModeling {...props} documentVersionId="replacement-pdf"/>);
  expect(screen.getByRole("alert").textContent).toContain("不属于当前 PDF 版本");
  expect((screen.getByRole("button", { name: "创建建模工作区" }) as HTMLButtonElement).disabled).toBe(true);
  expect(request).not.toHaveBeenCalled();
});

it("reuses the Paper Agent model and existing workspace for another figure", async () => {
  useWorldStore.setState({ cards: [{ id: "shared-agent", name: "Shared Agent", type: "atomsculptor.agent",
    position: { x: 0, y: 0 }, size: { width: 300, height: 400 }, expanded: false, status: "idle", config: {
    paper_id: "paper-1", research_projection: "paper_modeling", model: "oaw:model:vision",
  } } as typeof originalWorld.cards[number]] });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    paper_id: "paper-1", image_id: "image-2", agent_id: "shared-agent", structure_id: "shared-structure",
    model: "oaw:model:vision", replay: false, workspace_reused: true, run_prompt: "Use image-2 in a separate layer.",
  }), { status: 200 })));
  mount();
  expect((screen.getByRole("combobox", { name: "建模模型" }) as HTMLSelectElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "添加文献图并复用工作区" }));
  expect((await screen.findByRole("status")).textContent).toContain("已复用这篇论文");
  await waitFor(() => expect(useNodeSurfaceStore.getState().openWorkspace).toHaveBeenCalledWith("shared-structure"));
  expect(useNodeSurfaceStore.getState().openInspector).toHaveBeenCalledWith("shared-agent");
  expect(worldApi.runAgent).not.toHaveBeenCalled();
});

it("shows a revision conflict without reporting creation or opening cards", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: { error: { message: "Paper revision changed; retry." } } }), { status: 409 })));
  mount();
  fireEvent.click(screen.getByRole("button", { name: "创建建模工作区" }));
  expect((await screen.findByRole("alert")).textContent).toBe("Paper revision changed; retry.");
  expect(refreshWorld).not.toHaveBeenCalled();
  expect(useNodeSurfaceStore.getState().openWorkspace).not.toHaveBeenCalled();
  expect(screen.queryByRole("status")).toBeNull();
});
