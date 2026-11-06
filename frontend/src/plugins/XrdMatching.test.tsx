// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import xrd from "../../../plugins/xrd/frontend";
import { FocusLayer } from '../../../plugins/xrd/frontend/MatchControls';
import type { PluginViewProps } from "./sdk";
import { TEST_CATALOG } from "../state/catalog.fixture";

afterEach(cleanup);
it('keeps an open picker attached when its trigger is replaced during refresh', () => {
  const root = document.createElement('div');root.className = 'xrd-match-ui';
  const anchor = document.createElement('button');root.append(anchor);document.body.append(root);
  const close = vi.fn();
  const view = render(<FocusLayer anchor={anchor} kind="elements" onClose={close}><button>Li</button></FocusLayer>);
  try {
    anchor.remove();
    view.rerender(<FocusLayer anchor={anchor} kind="elements" onClose={close}><button>Li selected</button></FocusLayer>);
    expect(screen.getByRole('dialog', { name: '选择元素' }).textContent).toContain('Li selected');
    expect(close).not.toHaveBeenCalled();
  } finally { view.unmount();root.remove(); }
});
beforeEach(()=>{vi.stubGlobal('ResizeObserver',class{observe(){} disconnect(){}});vi.stubGlobal('matchMedia',(media:string)=>Object.assign(new EventTarget(),{matches:true,media,onchange:null,addListener(){},removeListener(){}}));});

const candidate = {
  node_id: "library:7222155", filename: "COD 7222155", metadata: { formula: "LiTi2(PO4)3" },
  peaks: [{ two_theta: 20, intensity: 100 }], matches: [{ observed_index: 0, observed: 20.04, reference: 20, delta: .04, hkl: [] }],
  reference_count: 1, mean_abs_delta: .04, score: .8, cifs: [],
};
const baseResult = {
  mode: "match", pattern: { filename: "sample.txt", points: [[10, 1], [20, 100], [30, 1]] },
  observed_peaks: [{ two_theta: 20.04, intensity: 100 }], candidates: [candidate],
};
function mount(result?: Record<string, unknown>, level: PluginViewProps["level"] = "inspector", overrides: Partial<PluginViewProps["host"]> = {}) {
  const host: PluginViewProps["host"] = {
    delegationAction: vi.fn(), resourceAction: vi.fn(), resourceFileAction: vi.fn(),
    runAnalysis: vi.fn().mockResolvedValue(undefined),
    updateConfig: vi.fn().mockResolvedValue(undefined), getAgentInfo: vi.fn().mockResolvedValue({ session_id: "", details: { result } }),
    listCards: vi.fn().mockResolvedValue([]), readDocument: vi.fn(), documentAction: vi.fn(), documentDownloadUrl: vi.fn(),
    transform: vi.fn(), readFile: vi.fn(), openFile: vi.fn(), clearOpenedFile: vi.fn(),
    openWorkspace: vi.fn(), runAgent: vi.fn().mockResolvedValue(undefined),
    onDocumentChange: vi.fn(() => () => {}), registerVisualCapture: vi.fn(() => () => {}),
  };
  Object.assign(host, overrides);
  const Settings = xrd.views.settings;
  render(<Settings host={host} level={level} definition={TEST_CATALOG.node_types[0]} card={{
    id: "match", type: "xrd.match", name: "Match", status: "idle", expanded: true,
    position: { x: 0, y: 0 }, size: { width: 400, height: 500 },
    config: { mode: "match", wavelength: 1.540593, tolerance_deg: .3, prominence_fraction: .03, reference_min_intensity: 5, smoothing_deg: .03, min_peak_distance: .1 },
  }} />);
  return host;
}

it("persists the bounded cell scale setting and explains QualX recall limits", async () => {
  const host=mount();
  const field=screen.getByRole('spinbutton',{name:'晶胞缩放范围 ± / %'});
  fireEvent.change(field,{target:{value:'1.5'}});fireEvent.blur(field);
  await waitFor(()=>expect(host.updateConfig).toHaveBeenCalledWith({cell_scale_percent:1.5}));
  expect(screen.getByRole('combobox',{name:'谱库检索方式'}).title).toContain('尺度搜索仅用于初筛候选');
});

it("shows the shared scale and original versus aligned residuals", async () => {
  mount({...baseResult,candidates:[{...candidate,alignment:{cell_scale:.993,cell_change_percent:-.7,
    at_limit:false,unscaled_score:.17,unscaled_match_count:3,unscaled_mean_abs_delta:.12},
    matches:[{...candidate.matches[0],original_reference:19.84,reference:20.02,delta:.02,raw_delta:.2}]}]});
  const cell=await screen.findByRole('cell',{name:'-0.700'});
  expect(cell.title).toContain('尺度因子 0.993000');
  expect(cell.title).toContain('未缩放得分 17.0');
  const summary=screen.getByText('匹配峰与偏差（1）');fireEvent.click(summary);
  const table=summary.closest('details')!;
  expect(table.textContent).toContain('19.840');expect(table.textContent).toContain('20.020');
  expect(table.textContent).toContain('0.2000');expect(table.textContent).toContain('0.0200');
});

it("defaults to QualX3 and persists switching to the native engine", async () => {
  const host = mount();
  const engine = screen.getByRole("combobox",{name:"谱库检索方式"});
  expect(engine.textContent).toContain("快速检索");
  fireEvent.click(engine);
  fireEvent.click(screen.getByRole("option",{name:/遍历检索/}));
  await waitFor(() => expect(host.updateConfig).toHaveBeenCalledWith({ library_engine: "native" }));
});

it("distinguishes the whole library from candidates screened and scored", async () => {
  mount({ ...baseResult, library_search: {
    enabled: true, engine: "qualx", total_records: 532995, screened_candidates: 28, scored_records: 25,
    scanned: 28, invalid_records: 3, elapsed_seconds: 1.234, retrieval_seconds: .9, top_n: 20,
  } });
  const table = await screen.findByRole('table', { name: '检索概况' });
  const value = (name:string) => within(table).getByRole('rowheader', { name }).nextElementSibling?.textContent;
  expect(value('检索方式')).toBe('快速检索');
  expect(value('谱库条目')).toBe('532,995');
  expect(value('耗时')).toBe('1.23 秒');
  expect(value('初筛候选')).toBe('28');
  expect(value('已比对')).toBe('25');
  expect(value('无效记录')).toBe('3');
  expect(screen.queryByText(/全库已检索/)).toBeNull();
  expect(screen.getByText("上次运行参数（结果快照）")).toBeTruthy();
});

it("keeps legacy native results readable without claiming a QualX3 run", async () => {
  mount({ ...baseResult, library_search: { enabled: true, scanned: 532995, invalid_records: 7, elapsed_seconds: 5.8, top_n: 20 } });
  const table = await screen.findByRole('table', { name: '检索概况' });
  const value = (name:string) => within(table).getByRole('rowheader', { name }).nextElementSibling?.textContent;
  expect(value('检索方式')).toBe('遍历检索');
  expect(value('耗时')).toBe('5.80 秒');
  expect(value('已遍历')).toBe('532,995');
  expect(value('谱库条目')).toBe('—');
  expect(within(table).queryByRole('rowheader', { name: '初筛候选' })).toBeNull();
});

it("shows an actionable empty search and no empty candidate selector", async () => {
  mount({ ...baseResult, candidates: [], library_search: {
    enabled: true, engine: "qualx", total_records: 532995, screened_candidates: 0, scored_records: 0,
    scanned: 0, invalid_records: 0, elapsed_seconds: .8, top_n: 20,
  } });
  expect(await screen.findByText("未找到可比对候选。可调整元素范围，或切换 遍历检索。")).toBeTruthy();
  expect(screen.queryByRole('table', { name: '候选匹配比较' })).toBeNull();
});

it("preserves manual reference results when QualX3 returns no library candidates", async () => {
  mount({ ...baseResult, library_search: {
    enabled: true, engine: "qualx", total_records: 532995, screened_candidates: 0, scored_records: 0,
    scanned: 0, invalid_records: 0, elapsed_seconds: .8, top_n: 20,
  } });
  expect(await screen.findByText("本次未召回谱库候选，以下为手动标准卡片的比对结果。")).toBeTruthy();
  expect(within(screen.getByRole('table', { name: '候选匹配比较' })).getByRole('button', { name: /COD 7222155/ })).toBeTruthy();
});

it("keeps all recorded parameters together without substituting the current configuration", async () => {
 mount({...baseResult, parameters:{wavelength:1.2,tolerance_deg:.15,prominence_fraction:.05,reference_min_intensity:7,smoothing_deg:.02,min_peak_distance:.2}, library_search:{enabled:true,engine:'qualx',scanned:1,invalid_records:0,elapsed_seconds:1,top_n:12,allowed_elements:['Li','O'],engine_runs:[{settings:{strongest_peaks:3,min_fom:.35,max_entries:3000}}]}});
 const summary=await screen.findByText('上次运行参数（结果快照）');
 const group=screen.getByRole('group',{name:'参数配置'});
 expect(group.contains(summary)).toBe(true);
 expect(group.textContent).toContain('1.2');
 expect(group.textContent).toContain('QualX 最低 FOM');
 expect(group.textContent).toContain('3000');
 expect((screen.getByLabelText('匹配波长 / Å') as HTMLInputElement).value).toBe('1.540593');
 expect(screen.queryByText(/本次参数：/)).toBeNull();
});

it("opens workspace on results and switches between the two workflow steps", async () => {
  mount(baseResult, "workspace");
  expect(await screen.findByText("标准卡片匹配结果")).toBeTruthy();
  expect(screen.queryByText("参数配置")).toBeNull();
  expect(screen.getByRole("img").classList.contains("is-interactive")).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "01 参数设置" }));
  expect(screen.getByRole("group",{name:"参数配置"})).toBeTruthy();
  expect(screen.queryByRole("img")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "02 匹配结果" }));
  const plot = screen.getByRole('img', { name: '实验谱与标准峰叠加图' });
  const line = plot.querySelector('line[stroke="#e5aa76"]')!;
  fireEvent.pointerEnter(line);
  expect(within(plot).getByRole("tooltip").textContent).toContain("标准峰 · 2θ 20.0000°");
  fireEvent.pointerLeave(line);
  expect(within(plot).queryByRole("tooltip")).toBeNull();
});

it("starts a new search on configuration with results unavailable", async () => {
  mount(undefined, "workspace");
  await waitFor(()=>expect(screen.getByRole('button',{name:"01 参数设置"}).getAttribute('aria-current')).toBe('step'));
  expect(screen.getByRole('group',{name:'参数配置'})).toBeTruthy();
  expect((screen.getByRole('button',{name:"02 匹配结果"}) as HTMLButtonElement).disabled).toBe(true);
});

it("uses back on results and play on settings without rerunning on back", async () => {
 const host=mount(baseResult,"workspace");
 fireEvent.click(await screen.findByRole('button',{name:'返回参数设置'}));
 expect(host.runAnalysis).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'重新检索'}));
 await waitFor(()=>expect(host.runAnalysis).toHaveBeenCalledOnce());
});

it("waits for pending parameter saves before launching", async()=>{
 let resolve!:()=>void;const saving=new Promise<void>(r=>{resolve=r;});
 const host=mount(undefined,"workspace",{updateConfig:vi.fn(()=>saving)});
 const input=screen.getByRole('spinbutton',{name:'候选数'});
 fireEvent.change(input,{target:{value:'12'}});fireEvent.blur(input);
 fireEvent.click(screen.getByRole('button',{name:'开始检索'}));
 await waitFor(()=>expect(host.updateConfig).toHaveBeenCalled());expect(host.runAnalysis).not.toHaveBeenCalled();
 resolve();await waitFor(()=>expect(host.runAnalysis).toHaveBeenCalledOnce());
});
it("does not run when saving parameters failed", async()=>{
 const host=mount(undefined,"workspace",{updateConfig:vi.fn().mockRejectedValue(new Error('save failed'))});
 const input=screen.getByRole('spinbutton',{name:'候选数'});
 fireEvent.change(input,{target:{value:'12'}});fireEvent.blur(input);
 await screen.findByText(/save failed/);fireEvent.click(screen.getByRole('button',{name:'开始检索'}));
 await screen.findByText(/参数有误/);expect(host.runAnalysis).not.toHaveBeenCalled();
});
it("blocks execution for missing authorized inputs",async()=>{
 const host=mount(undefined,"workspace",{getInputs:vi.fn().mockResolvedValue([])});
 await waitFor(()=>expect(host.getInputs).toHaveBeenCalled());
 expect(screen.queryByText('请连接一个实验谱')).toBeNull();
 expect((screen.getByRole('button',{name:'开始检索'}) as HTMLButtonElement).disabled).toBe(true);
});
