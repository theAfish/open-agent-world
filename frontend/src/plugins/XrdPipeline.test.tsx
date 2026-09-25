// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import xrd from '../../../plugins/xrd/frontend';
import type { PluginViewProps } from './sdk';
import { TEST_CATALOG } from '../state/catalog.fixture';

afterEach(cleanup);
beforeEach(() => { vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} }); vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener() {}, removeEventListener() {} })); });
const candidates = Array.from({ length: 12 }, (_, index) => ({
  node_id: `library:${index + 1}`, filename: `COD ${7222155 + index}`, metadata: { formula: `Phase ${index + 1}` },
  peaks: [{ two_theta: 20, intensity: 100 }], matches: [{ observed_index: 0, observed: 20.04, reference: 20, delta: .04, hkl: [] }],
  reference_count: 1, mean_abs_delta: .04, score: .8, cifs: [{ filename: `phase-${index + 1}.cif` }],
}));
const result = { mode: 'match', pattern: { filename: 'sample.txt', points: [[10, 1], [20, 100], [30, 1]] }, observed_peaks: [{ two_theta: 20.04, intensity: 100 }], candidates };
const output = (index: number, status = 'completed', extra = {}) => ({ candidate_id: candidates[index].node_id, label: candidates[index].filename, status, accepted: status !== 'rejected', output_cif: { filename: `out-${index}.cif`, source_base64: 'ZGF0YV90ZXN0', sha256: `output-${index}` }, source_sha256: `original-${index}`, starting_sha256: `starting-${index}`, ...extra });
const preopt = { run_id: 'preopt-1', status: 'completed', result: { mode: 'pipeline', stage: 'preopt', match_run_id: 'match-1', candidates: [output(0), output(1, 'rejected'), output(2, 'failed', { error: 'no cif', output_cif: undefined })] } };
function mount({ workflow = { match_run_id: 'match-1' }, config = {}, status = 'idle', last = {}, progress, host: overrides = {}, matchResult = result }: { workflow?: Record<string, unknown>; config?: Record<string, unknown>; status?: string; last?: Record<string, unknown>; progress?: unknown; host?: Partial<PluginViewProps['host']>; matchResult?: typeof result } = {}) {
  const host: PluginViewProps['host'] = {
    delegationAction: vi.fn(), resourceAction: vi.fn(),
    runAnalysis: vi.fn().mockResolvedValue(undefined), stopAnalysis: vi.fn().mockResolvedValue(undefined), updateConfig: vi.fn().mockResolvedValue(undefined),
    getAgentInfo: vi.fn().mockResolvedValue({ session_id: '', details: { result: matchResult, workflow, last_run: last, progress } }),
    listCards: vi.fn().mockResolvedValue([]), readDocument: vi.fn(), documentAction: vi.fn(), documentDownloadUrl: vi.fn(), transform: vi.fn(), readFile: vi.fn(), openFile: vi.fn(), clearOpenedFile: vi.fn(), openInputNode: vi.fn().mockResolvedValue(undefined), ...overrides,
  };
  const Settings = xrd.views.settings;
  render(<Settings host={host} level="workspace" definition={TEST_CATALOG.node_types[0]} card={{ id: 'match', type: 'xrd.match', name: 'Match', status, expanded: true, position: { x: 0, y: 0 }, size: { width: 1000, height: 800 }, config: { mode: 'match', wavelength: 1.540593, tolerance_deg: .3, prominence_fraction: .03, reference_min_intensity: 5, smoothing_deg: .03, min_peak_distance: .1, ...config } } as PluginViewProps['card']}/>);
  return host;
}

it('defaults a new match to its top three candidates and caps multi-selection at ten', async () => {
  const host = mount({ config: { workflow_match_run_id: 'old-match', selected_candidate_ids: ['library:10'] } });
  const selection = await screen.findByRole('region', { name: '后续优化候选' });
  const boxes = within(selection).getAllByRole('checkbox') as HTMLInputElement[];
  expect(boxes.filter(box => box.checked).map(box => box.getAttribute('aria-label'))).toEqual(candidates.slice(0, 3).map(candidate => `选择候选 ${candidate.filename}`));
  for (let index = 3; index < 10; index++) fireEvent.click(boxes[index]);
  expect(boxes.filter(box => box.checked)).toHaveLength(10); expect(boxes[10].disabled).toBe(true); expect(boxes[11].disabled).toBe(true);
  await waitFor(() => expect(host.updateConfig).toHaveBeenLastCalledWith({ selected_candidate_ids: candidates.slice(0, 10).map(candidate => candidate.node_id), workflow_match_run_id: 'match-1' }));
});

it('persists the selected batch and parent match before starting preoptimization', async () => {
  const host = mount();
  fireEvent.click(await screen.findByRole('button', { name: '单相模式' }));
  fireEvent.click(screen.getByRole('button', { name: '结构预优化设置' }));
  expect(screen.getByRole('spinbutton', { name: '预优化最大函数评估次数' }).getAttribute('value')).toBe('120');
  fireEvent.click(screen.getByRole('button', { name: '关闭结构预优化设置' }));
  fireEvent.click(screen.getByRole('button', { name: '开始预优化' }));
  await waitFor(() => expect(host.runAnalysis).toHaveBeenCalledOnce());
  expect(host.updateConfig).toHaveBeenLastCalledWith({ workflow_stage: 'preopt', workflow_match_run_id: 'match-1', selected_candidate_ids: ['library:1', 'library:2', 'library:3'] });
  expect(vi.mocked(host.updateConfig).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(host.runAnalysis!).mock.invocationCallOrder[0]);
});

it('fits accepted and rejected original structures while excluding failed candidates', async () => {
  const host = mount({ workflow: { match_run_id: 'match-1', preopt } });
  expect(await screen.findByText('保留原始 CIF')).toBeTruthy();
  expect(screen.getByText('不参与后续拟合')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '进入全谱拟合' }));
  fireEvent.click(screen.getByRole('button', { name: '开始全谱拟合' }));
  await waitFor(() => expect(host.runAnalysis).toHaveBeenCalledOnce());
  expect(host.updateConfig).toHaveBeenLastCalledWith({ workflow_stage: 'fit', workflow_match_run_id: 'match-1', workflow_preopt_run_id: 'preopt-1', selected_candidate_ids: ['library:1', 'library:2'] });
});

it('requires newly added candidates to be preoptimized before fitting', async () => {
  mount({ workflow: { match_run_id: 'match-1', preopt } });
  fireEvent.click(await screen.findByRole('button', { name: '02 匹配结果' }));
  fireEvent.click(screen.getByRole('checkbox', { name: `选择候选 ${candidates[3].filename}` }));
  fireEvent.click(screen.getByRole('button', { name: '单相模式' }));
  expect(screen.getByText('候选选择已改变，请重新运行预优化以包含新增候选。')).toBeTruthy();
  expect((screen.getByRole('button', { name: '进入全谱拟合' }) as HTMLButtonElement).disabled).toBe(true);
});

it('keeps a prior successful preoptimization visible after failure without silently fitting it', async () => {
  const host = mount({ workflow: { match_run_id: 'match-1', active_stage: 'preopt', preopt: { run_id: 'failed-attempt', status: 'failed', previous_success: preopt } }, last: { run_id: 'failed-attempt', status: 'failed', workflow_stage: 'preopt' } });
  expect(await screen.findByText(/以下为上次成功运行的结果/)).toBeTruthy();
  expect((screen.getByRole('button', { name: '进入全谱拟合' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '04 全谱拟合' }));
  expect((screen.getByRole('button', { name: '开始全谱拟合' }) as HTMLButtonElement).disabled).toBe(true);
  expect(host.runAnalysis).not.toHaveBeenCalled();
});

it('restores fitting results, displays convergence separately, and opens a linked output CIF', async () => {
  const fit = { run_id: 'fit-1', status: 'completed', result: { mode: 'pipeline', stage: 'fit', match_run_id: 'match-1', preopt_run_id: 'preopt-1', candidates: [output(0, 'completed', { converged: false, metrics: { rwp_percent: 12.5, rp_percent: 8.2 }, cell_before: [1, 2, 3, 90, 90, 120], cell_after: [1.1, 2.1, 3.1, 90, 90, 120], plot: { observed: [[10, 100], [20, 100]], calculated: [[10, 50], [20, 50]] } })] } };
  const host = mount({ workflow: { match_run_id: 'match-1', preopt, fit } });
  expect(await screen.findByText('12.500')).toBeTruthy(); expect(screen.getByText('未达到判据')).toBeTruthy();
  const plot = screen.getByRole('img', { name: '实验谱与全谱拟合结果' }); const lines = plot.querySelectorAll('polyline');
  expect(lines[0].getAttribute('points')).not.toBe(lines[1].getAttribute('points'));
  expect(screen.getByRole('table', { name: '晶胞参数变化' }).textContent).toContain('1.10000');
  fireEvent.click(screen.getByRole('button', { name: '打开结构画布' }));
  await waitFor(() => expect(host.openInputNode).toHaveBeenCalledWith('xrd.cif', 'COD 7222155 · 结构输出', { filename: 'out-0.cif', source_base64: 'ZGF0YV90ZXN0' }, 'xrd.input'));
});

it('does not mistake partial running results for a changed candidate selection', async () => {
  mount({ status: 'running', workflow: { match_run_id: 'match-1', active_stage: 'preopt', preopt: { ...preopt, status: 'running', result: { ...preopt.result, candidates: [output(0)] } } }, last: { status: 'running', workflow_stage: 'preopt' }, progress: { percent: 33, stage: '候选 2 / 3', completed: 1, total: 3 } });
  await screen.findByText('候选 2 / 3 · 1 / 3');
  expect(screen.queryByText('候选选择已改变，请重新运行预优化以包含新增候选。')).toBeNull();
  expect((screen.getByRole('button', { name: '进入全谱拟合' }) as HTMLButtonElement).disabled).toBe(true);
});

it('shows true progress on the preoptimization segment and unknown progress without a fake percentage', async () => {
  mount({ status: 'running', workflow: { match_run_id: 'match-1', active_stage: 'preopt', preopt: { run_id: 'p1', status: 'running' } }, last: { status: 'running', workflow_stage: 'preopt' }, progress: { percent: 45, stage: '候选 2 / 3', completed: 1, total: 3 } });
  await screen.findByText('候选 2 / 3 · 1 / 3');
  expect(screen.getByRole('status', { name: '结构预优化：运行中（任务活动指示）' }).getAttribute('data-state')).toBe('running');
  cleanup();
  mount({ status: 'running', workflow: { match_run_id: 'match-1', active_stage: 'preopt', preopt: { run_id: 'p1', status: 'running' } }, last: { status: 'running', workflow_stage: 'preopt' } });
  await screen.findByRole('button', { name: '停止结构预优化' });
  expect(screen.getByRole('status', { name: '结构预优化：运行中（任务活动指示）' }).hasAttribute('aria-valuenow')).toBe(false);
});

it('validates the fitting interval before launching and retains the current results', async () => {
  const host = mount({ workflow: { match_run_id: 'match-1', preopt }, config: { low_angle: 80, high_angle: 70 } });
  fireEvent.click(await screen.findByRole('button', { name: '进入全谱拟合' }));
  fireEvent.click(screen.getByRole('button', { name: '开始全谱拟合' }));
  expect(await screen.findByText('Error: 晶胞更新峰区间下限必须小于上限')).toBeTruthy();
  expect(host.runAnalysis).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '02 匹配结果' })); expect(screen.getByText('标准卡片匹配结果')).toBeTruthy();
});
