// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it } from "vitest";
import type { WorldCard, WorldEdge } from "../types/world";
import { useWorldStore } from "./worldStore";
import { useFileViewer, useOpenFiles } from "./openFiles";

const source = (id: string) => ({ id, name: id, type: "sandbox" }) as WorldCard;
const link = (target: string) => ({ id: target, source: "viewer", target, relationship: "core.file-preview", direction: "forward" }) as WorldEdge;
const open = (source_id: string, path: string) => useOpenFiles.getState().open({ kind: "sandbox", source_id, root: "workspace", path }, path);
beforeEach(() => {
  useOpenFiles.setState({ sequence: 0, sources: {}, pins: {} });
  useWorldStore.setState({ cards: [source("a"), source("b")], edges: [] });
});
afterEach(cleanup);

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
