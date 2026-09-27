// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CandidateStructure, StructureCanvas, codCandidateId } from "../../../plugins/xrd/frontend/CandidateStructure";
import { renderStructure } from "../../../plugins/structure_viewer/frontend/render";
import { prepareStructure } from '../../../plugins/structure_viewer/frontend/prepareStructure';
import type { PluginViewProps } from "./sdk";

vi.mock("../../../plugins/structure_viewer/frontend/render", () => ({ renderStructure: vi.fn(() => vi.fn()) }));
vi.mock("../../../plugins/structure_viewer/frontend/prepareStructure", () => ({ prepareStructure: vi.fn().mockResolvedValue({ sites: [] }) }));
const NativeURL = URL;
const createObjectURL = vi.fn();
const revokeObjectURL = vi.fn();
beforeEach(() => {
  vi.clearAllMocks();
  let sequence = 0;
  createObjectURL.mockImplementation(() => `blob:structure-${++sequence}`);
  vi.stubGlobal("URL", Object.assign(class extends NativeURL {}, { createObjectURL, revokeObjectURL }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function stored(cod_id = "7222155") {
  return { value: { structure: { cod_id, filename: `${cod_id}.cif`, source_base64: "ZGF0YV9jcnlzdGFs",
    source_url: `https://www.crystallography.net/cod/${cod_id}.cif`, local_path: `C:/XRD/structures/${cod_id}.cif` } }, revision: 8 };
}
function makeHost() {
  return {
    openInputNode: vi.fn().mockResolvedValue(undefined),
    readDocument: vi.fn().mockResolvedValue({ value: { structure: null }, revision: 7 }),
    documentAction: vi.fn().mockResolvedValue(stored()),
    documentDownloadUrl: vi.fn().mockReturnValue("/api/nodes/match/downloads/structure"),
  } as unknown as PluginViewProps["host"];
}

it("only identifies references whose COD number agrees with the COD source URL", () => {
  expect(codCandidateId({ reference_code: "7222155", url: "https://www.crystallography.net/cod/7222155.html" })).toBe("7222155");
  expect(codCandidateId({ reference_code: "7222155", url: "https://example.org/cod/7222155.html" })).toBeUndefined();
  expect(codCandidateId({ reference_code: "7222155", url: "https://www.crystallography.net/cod/1000001.html" })).toBeUndefined();
  expect(codCandidateId({ reference_code: "35-0754" })).toBeUndefined();
});

it("fetches the selected CIF using a fresh revision, renders its bytes and disposes the canvas", async () => {
  const host = makeHost();
  const view = render(<CandidateStructure codId="7222155" host={host} />);
  fireEvent.click(screen.getByRole("button", { name: "打开结构画布" }));
  await waitFor(() => expect(host.documentAction).toHaveBeenCalledWith("fetch_cod", { cod_id: "7222155" }, 7));
  expect(await screen.findByRole("link", {name:"下载 COD 7222155 CIF"})).toBeTruthy();
  expect(screen.getByRole("link", { name: "下载 COD 7222155 CIF" }).getAttribute("href")).toBe("blob:structure-1");
  await waitFor(() => expect(host.openInputNode).toHaveBeenCalledWith("xrd.cif", "COD 7222155 · 候选结构", {filename:"7222155.cif",source_base64:stored().value.structure.source_base64}, "xrd.input"));
  expect(renderStructure).not.toHaveBeenCalled();
  view.unmount();
});

it("restores a saved structure without a network fetch and ignores a saved different candidate", async () => {
  const host = makeHost();
  vi.mocked(host.readDocument).mockResolvedValue(stored());
  const view = render(<CandidateStructure key="7222155" codId="7222155" host={host} />);
  await screen.findByRole("button", {name:"打开结构画布"});
  expect(host.documentAction).not.toHaveBeenCalled();
  view.rerender(<CandidateStructure key="1000001" codId="1000001" host={host} />);
  await waitFor(() => expect(host.readDocument).toHaveBeenCalledTimes(2));
  expect(screen.queryByText(/已保存到本地/)).toBeNull();
  expect(screen.queryByRole("link", { name: /下载 COD/ })).toBeNull();
});

it("does not display a late candidate response after selection changes", async () => {
  const host = makeHost();
  let finish: (value: ReturnType<typeof stored>) => void = () => {};
  vi.mocked(host.documentAction).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  const view = render(<CandidateStructure key="7222155" codId="7222155" host={host} />);
  fireEvent.click(screen.getByRole("button", { name: "打开结构画布" }));
  await waitFor(() => expect(host.documentAction).toHaveBeenCalledOnce());
  view.rerender(<CandidateStructure key="1000001" codId="1000001" host={host} />);
  await act(async () => finish(stored()));
  expect(screen.getByRole("region", { name: "候选结构 COD 1000001" })).toBeTruthy();
  expect(screen.queryByText(/已保存到本地/)).toBeNull();
  expect(renderStructure).not.toHaveBeenCalled();
});

it("lets the user retry failed downloads with the latest document revision", async () => {
  const host = makeHost();
  vi.mocked(host.documentAction).mockRejectedValueOnce(new Error("COD 暂时无法连接"));
  render(<CandidateStructure codId="7222155" host={host} />);
  fireEvent.click(screen.getByRole("button", { name: "打开结构画布" }));
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", "COD 暂时无法连接");
  vi.mocked(host.readDocument).mockResolvedValue({ value: { structure: null }, revision: 9 });
  fireEvent.click(screen.getByRole("button", { name: "打开结构画布" }));
  await waitFor(() => expect(host.documentAction).toHaveBeenLastCalledWith("fetch_cod", { cod_id: "7222155" }, 9));
  expect(await screen.findByRole("link", {name:"下载 COD 7222155 CIF"})).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
});

it("downloads the visible candidate's exact bytes and revokes its URL on switching or unmounting", async () => {
  const host = makeHost();
  const first = stored();
  const original = new Uint8Array([100, 97, 116, 97, 95, 99, 10, 35, 32, 0xc3, 0xa9, 10]);
  first.value.structure.source_base64 = btoa(String.fromCharCode(...original));
  vi.mocked(host.readDocument).mockResolvedValue(first);
  const view = render(<CandidateStructure key="7222155" codId="7222155" host={host} />);
  const firstLink = await screen.findByRole("link", { name: "下载 COD 7222155 CIF" });
  expect(firstLink.getAttribute("href")).toBe("blob:structure-1");
  expect(firstLink.getAttribute("download")).toBe("7222155.cif");
  const blob = createObjectURL.mock.calls[0][0] as Blob;
  expect(blob.type).toBe("chemical/x-cif");
  const content = await new Promise<ArrayBuffer>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(blob);
  });
  expect(new Uint8Array(content)).toEqual(original);
  expect(host.documentDownloadUrl).not.toHaveBeenCalled();
  vi.mocked(host.readDocument).mockResolvedValue(stored("1000001"));
  view.rerender(<CandidateStructure key="1000001" codId="1000001" host={host} />);
  const nextLink = await screen.findByRole("link", { name: "下载 COD 1000001 CIF" });
  expect(nextLink.getAttribute("href")).toBe("blob:structure-2");
  expect(revokeObjectURL).toHaveBeenCalledWith("blob:structure-1");
  view.unmount();
  expect(revokeObjectURL).toHaveBeenCalledWith("blob:structure-2");
});

it("renders and disposes the independent CIF canvas", async () => {
 const view=render(<StructureCanvas structure={stored().value.structure}/>);
 await waitFor(()=>expect(renderStructure).toHaveBeenCalledOnce());
 const dispose=vi.mocked(renderStructure).mock.results[0].value;
 view.unmount(); expect(dispose).toHaveBeenCalledOnce();
});

it('ignores a slow old CIF parse when the user selects another candidate', async () => {
  let finish!: (value: Awaited<ReturnType<typeof prepareStructure>>) => void;
  vi.mocked(prepareStructure).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const onReady = vi.fn();
  const view = render(<StructureCanvas structure={stored().value.structure} onReady={onReady}/>);
  await waitFor(() => expect(prepareStructure).toHaveBeenCalledOnce());
  view.rerender(<StructureCanvas structure={stored('1000001').value.structure} onReady={onReady}/>);
  await waitFor(() => expect(onReady).toHaveBeenCalledOnce());
  await act(async () => finish({ sites: [] } as unknown as Awaited<ReturnType<typeof prepareStructure>>));
  expect(renderStructure).toHaveBeenCalledOnce();
  expect(vi.mocked(renderStructure).mock.calls[0][1].name).toBe('1000001.cif');
});

it('ends the loading state and preserves the error when structure parsing fails', async () => {
  vi.mocked(prepareStructure).mockRejectedValueOnce(new Error('invalid CIF'));
  const onReady = vi.fn();
  render(<StructureCanvas structure={stored().value.structure} onReady={onReady}/>);
  expect((await screen.findByRole('alert')).textContent).toContain('invalid CIF');
  expect(onReady).toHaveBeenCalledOnce();
  expect(screen.queryByText('正在绘制结构…')).toBeNull();
});

it("fetches a CIF for download without opening a canvas and orders the three actions", async () => {
  const host = makeHost();
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  render(<CandidateStructure codId="7222155" host={host}/>);
  fireEvent.click(screen.getByRole("button", {name:"下载 COD 7222155 CIF"}));
  await waitFor(()=>expect(click).toHaveBeenCalledOnce());
  expect(host.openInputNode).not.toHaveBeenCalled();
  expect(Array.from(document.querySelectorAll('.xrd-structure-toolbar > *')).map(el=>el.getAttribute('aria-label'))).toEqual(['查看 COD 结构与文献','下载 COD 7222155 CIF','打开结构画布']);
  expect(screen.queryByText(/已保存到本地/)).toBeNull();
  click.mockRestore();
});

it('skips parsing and rendering candidates superseded during the reaction window', async () => {
  const view = render(<StructureCanvas structure={stored().value.structure}/>);
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 100)); });
  expect(prepareStructure).not.toHaveBeenCalled();
  view.rerender(<StructureCanvas structure={stored('1000001').value.structure}/>);
  await waitFor(() => expect(renderStructure).toHaveBeenCalledOnce());
  expect(prepareStructure).toHaveBeenCalledOnce();
  expect(vi.mocked(renderStructure).mock.calls[0][1].name).toBe('1000001.cif');
});
