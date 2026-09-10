// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useOpenFiles } from "../state/openFiles";
import { useWorldStore } from "../state/worldStore";
import { ConversationAttachments } from "../cards/ConversationAttachments";
import plugin from "../../../plugins/structure_viewer/frontend/index";
import { renderStructure } from "../../../plugins/structure_viewer/frontend/render";
import type { PluginViewProps } from "./sdk";

vi.mock("../../../plugins/structure_viewer/frontend/render", () => ({ renderStructure: vi.fn(() => vi.fn()) }));
const props = { card: { id: "viewer" }, host: { readFile: vi.fn() }, level: "workspace" } as unknown as PluginViewProps;
const Viewer = plugin.views.viewer;
const open = (path: string) => useOpenFiles.getState().open({ kind: "sandbox", source_id: "source", root: "workspace", path }, path);
beforeEach(() => {
  vi.clearAllMocks();
  useOpenFiles.setState({ sequence: 0, sources: {}, pins: {} });
  useWorldStore.setState({ cards: [{ id: "source", name: "Files" } as never],
    edges: [{ id: "edge", source: "viewer", target: "source", relationship: "core.file-preview" } as never] });
});
afterEach(cleanup);

it("does not render a stale response after switching files or disconnecting", async () => {
  const responses: ((value: { name: string; data: string; size_bytes: number }) => void)[] = [];
  vi.mocked(props.host.readFile).mockImplementation(() => new Promise(resolve => responses.push(resolve)));
  open("first.cif"); render(<Viewer {...props} />);
  act(() => open("second.cif"));
  await act(async () => responses[1]({ name: "second.cif", data: "", size_bytes: 0 }));
  await waitFor(() => expect(renderStructure).toHaveBeenCalledOnce());
  await act(async () => responses[0]({ name: "first.cif", data: "", size_bytes: 0 }));
  expect(renderStructure).toHaveBeenCalledOnce();
  expect(vi.mocked(renderStructure).mock.calls[0][1].name).toBe("second.cif");
  act(() => open("third.cif"));
  act(() => useWorldStore.setState({ edges: [] }));
  await act(async () => responses[2]({ name: "third.cif", data: "", size_bytes: 0 }));
  expect(renderStructure).toHaveBeenCalledOnce();
  expect(screen.getByText("Connect this card to a Sandbox or Conversation")).toBeTruthy();
});

it("opens Conversation files without starting a download", () => {
  render(<ConversationAttachments conversationId="room" sessionId="session" files={[
    { name: "cell.cif", path: "cell.cif", version_id: "version", size_bytes: 100, media_type: "chemical/x-cif" },
  ]} />);
  fireEvent.click(screen.getByRole("button", { name: "Open cell.cif" }));
  expect(useOpenFiles.getState().sources.room.reference).toEqual({ kind: "conversation", source_id: "room",
    session_id: "session", version_id: "version", path: "cell.cif" });
  expect(screen.getByRole("link", { name: "Download cell.cif" }).getAttribute("download")).toBe("cell.cif");
});
