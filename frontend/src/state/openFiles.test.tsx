// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it } from "vitest";
import type { WorldCard, WorldEdge } from "../types/world";
import { useWorldStore } from "./worldStore";
import { FileWorkspaceContext, useFileViewer, useOpenFiles } from "./openFiles";

const source = (id: string) => ({ id, name: id, type: "sandbox" }) as WorldCard;
const link = (target: string) => ({ id: target, source: "viewer", target, relationship: "core.file-preview", direction: "forward" }) as WorldEdge;
const open = (source_id: string, path: string) => useOpenFiles.getState().open({ kind: "sandbox", source_id, root: "workspace", path }, path);
beforeEach(() => {
  useOpenFiles.setState({ sequence: 0, sources: {}, pins: {} });
  useWorldStore.setState({ cards: [source("a"), source("b")], edges: [] });
});
afterEach(cleanup);

it('restores each workspace selection and pin without leaking another scope', () => {
  useWorldStore.setState({ edges: [link('a')] });
  useOpenFiles.getState().open({ kind: 'sandbox', source_id: 'a', root: 'workspace', path: 'sessions/one/a.xyz' }, 'Session A', { workspaceKey: 'one' });
  useOpenFiles.getState().open({ kind: 'sandbox', source_id: 'a', root: 'workspace', path: 'groups/two/b.xyz' }, 'Group B', { workspaceKey: 'two' });
  let scope = 'one';
  const { result, rerender } = renderHook(() => useFileViewer('viewer'), {
    wrapper: ({ children }) => <FileWorkspaceContext.Provider value={scope}>{children}</FileWorkspaceContext.Provider>,
  });
  expect(result.current.file?.name).toBe('Session A');
  act(() => result.current.setPinned(true));
  scope = 'two'; rerender();
  expect(result.current.file?.name).toBe('Group B');
  expect(result.current.pinned).toBe(false);
  scope = 'one'; rerender();
  expect(result.current.file?.name).toBe('Session A');
  expect(result.current.pinned).toBe(true);
});

it('follows moved folder paths in pinned previews and clears only deleted descendants', () => {
  open('a', 'folder/child.txt');
  useOpenFiles.getState().pin('viewer', useOpenFiles.getState().sources.a);
  open('a', 'unrelated.txt'); open('b', 'folder/child.txt');
  useOpenFiles.getState().changeSandboxPath('a', 'folder', 'destination/folder');
  expect(useOpenFiles.getState().pins.viewer?.reference).toMatchObject({ path: 'destination/folder/child.txt' });
  expect(useOpenFiles.getState().sources.a.reference).toMatchObject({ path: 'unrelated.txt' });
  useOpenFiles.getState().changeSandboxPath('a', 'destination');
  expect(useOpenFiles.getState().pins.viewer).toBeUndefined();
  expect(useOpenFiles.getState().sources.b.reference).toMatchObject({ path: 'folder/child.txt' });
});

it("picks up files opened before connecting and follows the most recent connected source", () => {
  open("a", "first.cif"); open("b", "other.xyz");
  const { result } = renderHook(() => useFileViewer("viewer"));
  expect(result.current.file).toBeUndefined();
  act(() => useWorldStore.setState({ edges: [link("a")] }));
  expect(result.current.file?.name).toBe("first.cif");
  act(() => useWorldStore.setState({ edges: [link("a"), link("b")] }));
  expect(result.current.file?.name).toBe("other.xyz");
  act(() => open("a", "latest.cif"));
  expect(result.current.file?.name).toBe("latest.cif");
});

it("pins across selections but clears on revocation, without restoring an old pin on reconnect", () => {
  useWorldStore.setState({ edges: [link("a")] }); open("a", "fixed.cif");
  const { result } = renderHook(() => useFileViewer("viewer"));
  act(() => result.current.setPinned(true)); act(() => open("a", "new.cif"));
  expect(result.current.file?.name).toBe("fixed.cif");
  act(() => useWorldStore.setState({ edges: [] }));
  expect(result.current.file).toBeUndefined();
  act(() => useWorldStore.setState({ edges: [link("a")] }));
  expect(result.current.file?.name).toBe("new.cif");
  expect(result.current.pinned).toBe(false);
  act(() => useOpenFiles.getState().clear("a"));
  expect(result.current.file).toBeUndefined();
});
