// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { PipelineWorkflow } from '../../../plugins/xrd/frontend/PipelineView';
import { MultiphasePanel, useMultiphase } from '../../../plugins/xrd/frontend/Multiphase';
import { publishMultiphaseState, resolveMultiphaseSelection, useMultiphaseCanvas } from '../../../plugins/xrd/frontend/MultiphaseCanvasState';
import type { PluginViewProps, XrdMultiphaseState, XrdMultiphaseTrial } from './sdk';
import { TEST_CATALOG } from '../state/catalog.fixture';

beforeEach(() => {
  publishMultiphaseState('match-42', {status:'idle'}, false);
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

it('keeps the review timeline without the removed metric summary and removal table', async () => {
  mount({status:'completed', run_id:'multi-review', source_match_run_id:'search-42', pywpem_review:{status:'completed',
    reviews:[{full:{status:'completed',candidate_ids:['a','b'],labels:['A','B'],metrics:{rwp_percent:31.2,rp_percent:22.1},converged:false},
      removals:[{omitted_candidate_id:'a',status:'completed',delta_rwp:-2},{omitted_candidate_id:'b',status:'failed',error:'invalid cell'}]}]}});
  fireEvent.click(await screen.findByRole('button', {name:'04 联合复核'}));
  const section = await screen.findByRole('region', {name:'OAW_XRDfit 联合复核'});
  expect(within(section).getByText('联合全谱复核')).toBeTruthy();
  expect(within(section).queryByText(/31.200/)).toBeNull();
  expect(within(section).queryByRole('table')).toBeNull();
  expect(within(section).queryByText(/未达到收敛判据/)).toBeNull();
});

const candidates = Array.from({ length: 7 }, (_, index) => ({ node_id: `cod:${index}`, filename: `COD ${index}`, metadata: { formula: `Phase ${index}` }, peaks: [], matches: [], reference_count: 1, mean_abs_delta: .02, score: 80 }));
function mount(initial: XrdMultiphaseState = { status: 'idle' }, overrides: Partial<PluginViewProps['host']> = {}) {
  let state = initial;
  const host: PluginViewProps['host'] = {
    delegationAction: vi.fn(), resourceAction: vi.fn(),
    getAgentInfo: vi.fn().mockResolvedValue({ session_id: '', details: { result: { mode: 'match', candidates }, workflow: { match_run_id: 'search-42' } } }),
    getMultiphase: vi.fn(async () => state),
    startMultiphase: vi.fn(async () => { state = { status: 'running', run_id: 'multi-1', source_match_run_id: 'search-42', trials: [] }; }),
    stopMultiphase: vi.fn(async () => { state = { ...state, status: 'cancelled' }; }),
    updateConfig: vi.fn().mockResolvedValue(undefined),
    listCards: vi.fn(), readDocument: vi.fn(), documentAction: vi.fn(), documentDownloadUrl: vi.fn(), transform: vi.fn(), readFile: vi.fn(), openFile: vi.fn(), clearOpenedFile: vi.fn(), ...overrides,
  };
  render(<PipelineWorkflow host={host} level="workspace" definition={TEST_CATALOG.node_types[0]} renderResult={() => <div>单候选结果</div>} renderParameters={() => null} card={{ id: 'match-42', type: 'xrd.match', name: 'Match', status: 'idle', expanded: true, position: { x: 0, y: 0 }, size: { width: 1000, height: 800 }, config: { mode: 'match', workflow_match_run_id: 'search-42', selected_candidate_ids: ['cod:0', 'cod:1', 'cod:2'] } } as PluginViewProps['card']}/>);
  return host;
}

async function openMode() { fireEvent.click(await screen.findByRole('button', { name: '多相模式' })); fireEvent.click(await screen.findByRole('button', { name: '下次运行设置' })); }

it('selects Jev for the next run without relabeling existing LLM results', async () => {
  const host = mount({status:'completed',run_id:'old',source_match_run_id:'search-42',controller:{agent_node_id:'llm',name:'原 LLM 优化员'},available_agents:[{agent_node_id:'jev',name:'Jev 组合优化员'}]});
  await openMode();
  fireEvent.change(await screen.findByLabelText('优化 Agent'), {target:{value:'jev'}});
  expect(screen.queryByRole('option',{name:'原 LLM 优化员'})).toBeNull();
  expect(screen.getByRole('button',{name:'LLM Agent'})).toBeTruthy();
  expect(host.startMultiphase).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'重新搜索'}));
  await waitFor(()=>expect(host.startMultiphase).toHaveBeenCalledWith(expect.objectContaining({agent_node_id:'jev',restart:true})));
});

it('shows persisted Jev request quota and current draft without inventing progress for old runs', async () => {
  mount({status:'running',protocol_version:'jev_autoregressive_v2',optimizer_label:'Jev',cloud_request_count:7,cloud_request_limit:72,draft_candidate_ids:['cod:0','cod:2'],pool:[{candidate_id:'cod:0',label:'COD 0 · Phase A'},{candidate_id:'cod:2',label:'COD 2 · Phase C'}]});
  await screen.findByRole('button',{name:'停止筛选'});
  expect(screen.queryByRole('status',{name:'Jev 选相进度'})).toBeNull();
});

it('locks optimizer selection while running and labels Jev and BO separately', async () => {
  mount({status:'running',optimizer_label:'Jev 1.13.0',controller:{agent_node_id:'jev',name:'Jev 组合优化员'},available_agents:[{agent_node_id:'jev',name:'Jev 组合优化员'}]});
  fireEvent.click(await screen.findByRole('button',{name:'下次运行设置'}));
  const selector=await screen.findByLabelText('优化 Agent') as HTMLSelectElement;
  expect(selector.disabled).toBe(true);
  expect(selector.value).toBe('jev');
  expect(screen.getByRole('button',{name:'Jev 1.13.0'})).toBeTruthy();
  expect(screen.getByRole('button',{name:'BO_baseline'})).toBeTruthy();
  expect(screen.queryByRole('button',{name:'LLM Agent'})).toBeNull();
  const trial={trial_id:'j1',iteration:1,candidate_ids:['a'],status:'completed',score:80};
  expect(resolveMultiphaseSelection({status:'completed',optimizer_label:'Jev 1.13.0',trials:[trial]})?.caption).toBe('Jev 1.13.0 · 第 1 轮');
});

it('keeps shared matching results until the user chooses a branch and never starts on selection', async () => {
  const host = mount();
  await screen.findByText('单候选结果');
  expect(screen.getByRole('button', { name: '多相模式' }).getAttribute('aria-pressed')).toBe('false');
  expect(screen.queryByRole('region', { name: '多相自动筛选与联合拟合' })).toBeNull();
  expect(screen.getAllByRole('button', { name: '01 参数设置' })).toHaveLength(1);
  expect(screen.getAllByRole('button', { name: '02 匹配结果' })).toHaveLength(1);
  await openMode();
  expect(screen.getByRole('region', { name: '多相自动筛选与联合拟合' })).toBeTruthy();
  expect(screen.getByRole('button', {name:'03 组合筛选'}).getAttribute('aria-current')).toBe('step');
  expect(host.startMultiphase).not.toHaveBeenCalled();
  expect(document.querySelector('.xrd-multiphase-glow')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '01 参数设置' }));
  expect(screen.queryByRole('button', { name: '多相模式' })).toBeNull();
});

it('launches with the complete candidate pool and selected evaluation budget, once', async () => {
  const host = mount();
  await openMode();
  const launch = await screen.findByRole('button', { name: '开始多相优化' });
  expect(launch.closest('.xrd-workflow-actions')).toBeTruthy();
  expect(launch.textContent).toBe('');
  expect(document.querySelector('.xrd-multiphase > footer button')).toBeNull();
  fireEvent.change(screen.getByLabelText('每个算法的评估预算'), { target: { value: '32' } });
  fireEvent.change(screen.getByLabelText('每个组合最多物相'), { target: { value: '4' } });
  fireEvent.click(launch); fireEvent.click(launch);
  await waitFor(() => expect(host.startMultiphase).toHaveBeenCalledOnce());
  expect(host.startMultiphase).toHaveBeenCalledWith({ source_match_run_id: 'search-42', candidate_ids: candidates.map(item => item.node_id), budget: 32, max_phases: 4, evaluate_baseline: true });
  await screen.findByRole('button', { name: '停止筛选' });
  expect(document.querySelector('.xrd-multiphase-glow')?.getAttribute('data-phase')).toBe('running');
});

it('requires explicit stop before leaving an active multiphase run', async () => {
  const host = mount({ status: 'running', run_id: 'multi-1', source_match_run_id: 'search-42' });
  await screen.findByRole('button', { name: '停止筛选' });
  expect((screen.getByRole('button', { name: '02 匹配结果' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '停止筛选' }));
  await waitFor(() => expect(host.stopMultiphase).toHaveBeenCalledOnce());
  await waitFor(() => expect((screen.getByRole('button', { name: '02 匹配结果' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '02 匹配结果' }));
  await waitFor(() => expect(screen.queryByRole('region', { name: '多相自动筛选与联合拟合' })).toBeNull());
  expect(screen.getByText('单候选结果')).toBeTruthy();
});

it('reopens completed evaluations without silently starting another agent run', async () => {
  const host = mount({ status: 'completed', run_id: 'multi-1', source_match_run_id: 'search-42', trials: [] });
  await openMode();
  await screen.findByRole('button', { name: '重新搜索' });
  fireEvent.click(screen.getByRole('button', { name: '02 匹配结果' }));
  await screen.findByText('单候选结果');
  fireEvent.click(screen.getByRole('button', { name: '多相模式' }));
  await screen.findByRole('button', { name: '重新搜索' });
  expect(host.startMultiphase).not.toHaveBeenCalled();
});

it('keeps an actionable launch failure visible with no fabricated evaluations', async () => {
  mount({ status: 'idle' }, { startMultiphase: vi.fn().mockRejectedValue(new Error('模型连接不可用')) });
  await openMode();
  fireEvent.click(await screen.findByRole('button', { name: '开始多相优化' }));
  expect((await screen.findByRole('alert')).textContent).toContain('模型连接不可用');
  expect(screen.getByText('暂无评估记录')).toBeTruthy();
  expect(document.querySelector('.xrd-multiphase-glow')).toBeNull();
});

it('renders a single-phase winner, failed trials and baseline metrics without duplicating the shared spectrum canvas', async () => {
  const single: XrdMultiphaseTrial = { trial_id: 'trial-1', iteration: 1, candidate_ids: ['cod:0'], labels: ['Phase A'], status: 'completed', score: 89.2, metrics: { rwp_percent: 11.2, rp_percent: 8.1 }, plot: { observed: [[10, 100], [20, 100]], calculated: [[10, 90], [20, 90]], contributions: [{ candidate_id: 'cod:0', label: 'Phase A', points: [[10, 45], [20, 45]] }] } };
  const failed: XrdMultiphaseTrial = { trial_id: 'trial-2', iteration: 2, candidate_ids: ['cod:0', 'cod:1'], labels: ['Phase A', 'Phase B'], status: 'failed', error: '候选 CIF 无法读取' };
  const baseline = { ...single, trial_id: 'bo-1', score: 85.4 };
  render(<MultiphasePanel state={{ status: 'completed', run_id: 'multi-1', trials: [single, failed], incumbent: single, baseline: { status: 'completed', trials: [baseline], incumbent: baseline }, stop_reason: '已达到评估预算' }} options={{ budget: 24 }} onOptions={vi.fn()} onStart={vi.fn()} onStop={vi.fn()} candidateCount={7} canStart error=""/>);
  expect(screen.getByLabelText('最佳组合')).toBeTruthy();
  expect(screen.queryByRole('img', { name: '多相联合拟合与各物相贡献' })).toBeNull();
  expect(screen.queryByRole('table', { name: '多相优化算法比较' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: /第 2 轮 · Phase A \+ Phase B/ }));
  expect(screen.getByRole('alert').textContent).toBe('候选 CIF 无法读取');
  expect(screen.queryByRole('img', { name: '多相联合拟合与各物相贡献' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'BO_baseline' }));
  fireEvent.click(screen.getByText('查看组合评估表'));
  expect(within(screen.getByRole('table', { name: '多相组合评估记录' })).getByText('85.400')).toBeTruthy();
});

const completeTrial: XrdMultiphaseTrial = { trial_id: 'llm-1', iteration: 1, candidate_ids: ['cod:0'], labels: ['Phase A'], status: 'completed', score: 78.2, metrics: { rwp_percent: 16.5, rp_percent: 12.4 } };
const panelActions = { options: { budget: 24 }, onOptions: vi.fn(), onStart: vi.fn(), onStop: vi.fn(), candidateCount: 7, canStart: true, error: '' };

it('labels an old search snapshot as historical rather than the current best', async () => {
  mount({ status: 'completed', run_id: 'old-run', source_match_run_id: 'old-search', incumbent: completeTrial, trials: [completeTrial] });
  await openMode();
  expect(await screen.findByRole('note')).toHaveProperty('textContent', expect.stringContaining('以下结果来自先前检索'));
  expect(screen.queryByRole('region', { name: '综合最佳组合' })).toBeNull();
  expect(screen.getByLabelText('最佳组合')).toBeTruthy();
  expect(screen.getByRole('button', { name: '重新搜索' })).toBeTruthy();
});

it('uses the better BO result as the overall winner while preserving both optimizer results', () => {
  const bo = { ...completeTrial, trial_id: 'bo-1', labels: ['Phase B'], score: 84.9, validation: { metrics: { rwp_percent: 13.7 } } };
  render(<MultiphasePanel {...panelActions} state={{ status: 'completed', run_id: 'run-1', incumbent: completeTrial, trials: [completeTrial], baseline: { status: 'completed', incumbent: bo, trials: [bo] } }}/>);
  expect(screen.getByRole('button', { name: /Phase B.*综合最佳组合/ })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'BO_baseline' }).getAttribute('aria-pressed')).toBe('true');
});

it('shows the completed LLM lane while BO is still running', () => {
  render(<MultiphasePanel {...panelActions} state={{ status: 'running', llm_status: 'completed', trials: [completeTrial], incumbent: completeTrial, baseline: { status: 'running' } }}/>);
  expect(screen.getByRole('button', {name:/Phase A.*完成/})).toBeTruthy();
  expect(screen.getByRole('button', {name:'停止筛选'})).toBeTruthy();
});

it('shows holdout and full-profile metrics separately, with actual convergence and refinement bounds', () => {
  const trial: XrdMultiphaseTrial = {
    ...completeTrial, converged: true, validation: { metrics: { rwp_percent: 21.3 }, holdout_count: 701, independent_experiment: false },
    fit: { converged: false, zero_shift_deg: .02, fwhm_deg: .15, lorentz_fraction: .4, parameter_bounds: { zero_shift_deg: [-.3, .3], isotropic_cell_scale: [.995, 1.005] }, atomic_fractional_coordinates_fixed: true, reflection_relative_intensities_fixed: true },
    phase_contributions: [{ candidate_id: 'cod:0', formula: 'LiTi2P3O12', profile_area_fraction: .73, isotropic_cell_scale: 1.002, cell_input: [8, 8, 20], cell_fitted: [8.016, 8.016, 20.04] }],
  };
  render(<MultiphasePanel {...panelActions} state={{ status: 'completed', incumbent: trial, trials: [trial] }}/>);
  const detail = screen.getByRole('region', { name: '选中组合详情' });
  expect(within(detail).getByText('留出 Rwp / %').nextElementSibling?.textContent).toBe('21.300');
  expect(within(detail).getByText('全谱 Rwp / %').nextElementSibling?.textContent).toBe('16.500');
  expect(within(detail).getByText('拟合收敛').nextElementSibling?.textContent).toBe('未达到判据');
  expect(within(detail).queryByText(/留出采样点来自同一张实验谱/)).toBeNull();
  expect(within(detail).getByRole('table', { name: '多相晶胞精修结果' }).textContent).toContain('1.00200');
  expect(within(detail).getByRole('table', { name: '多相拟合参数约束' }).textContent).toContain('0.99500 – 1.00500');
});

it('blocks an oversized candidate pool explicitly without silently truncating it', async () => {
  const large = Array.from({ length: 31 }, (_, index) => ({ ...candidates[0], node_id: `cod:${index}` }));
  const host = mount({ status: 'idle' }, { getAgentInfo: vi.fn().mockResolvedValue({ session_id: '', details: { result: { mode: 'match', candidates: large }, workflow: { match_run_id: 'search-42' } } }) });
  await openMode();
  const launch = await screen.findByRole('button', { name: '开始多相优化' });
  expect(launch.closest('.xrd-workflow-actions')).toBeTruthy();
  expect(launch.textContent).toBe('');
  expect(document.querySelector('.xrd-multiphase > footer button')).toBeNull();
  expect((launch as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByRole('alert').textContent).toContain('当前检索有 31 个候选，多相模式最多支持 30 个');
  fireEvent.click(launch);
  expect(host.startMultiphase).not.toHaveBeenCalled();
});

it('polls running history no faster than 2.5 seconds and clears recovered connection failures', async () => {
  vi.useFakeTimers();
  const getMultiphase = vi.fn().mockRejectedValueOnce(new Error('暂时离线')).mockResolvedValue({ status: 'running' });
  const host = { getMultiphase } as unknown as PluginViewProps['host'];
  function Probe() { const state = useMultiphase(host); return <div>{state.error || state.state.status}</div>; }
  await act(async () => { render(<Probe/>); });
  expect(screen.getByText('Error: 暂时离线')).toBeTruthy();
  await act(async () => { vi.advanceTimersByTime(5000); });
  expect(screen.queryByText('Error: 暂时离线')).toBeNull();
  expect(getMultiphase).toHaveBeenCalledTimes(2);
  await act(async () => { vi.advanceTimersByTime(2499); });
  expect(getMultiphase).toHaveBeenCalledTimes(2);
  await act(async () => { vi.advanceTimersByTime(1); });
  expect(getMultiphase).toHaveBeenCalledTimes(3);
});

it('offers to continue an interrupted Agent on the same search snapshot without relabeling it as a new run', async () => {
  const host = mount({ status: 'interrupted', resumable: true, run_id: 'multi-1', source_match_run_id: 'search-42', trials: [completeTrial], incumbent: completeTrial });
  await openMode();
  const resume = await screen.findByRole('button', { name: '继续筛选' });
  expect((resume as HTMLButtonElement).disabled).toBe(false);
  expect(screen.queryByRole('button', { name: '重新运行' })).toBeNull();
  fireEvent.click(resume);
  await waitFor(() => expect(host.startMultiphase).toHaveBeenCalledOnce());
});

it('shares explicit bar selection with linked canvases and keeps review metrics out of search scores', async () => {
  const first: XrdMultiphaseTrial = { ...completeTrial, trial_id:'cursor-first', labels:['COD 1537407 · WCl4O'] };
  const second: XrdMultiphaseTrial = { ...completeTrial, trial_id:'cursor-second', iteration:2, candidate_ids:['cod:0','cod:1'], labels:['COD 1537407 · WCl4O','COD 9001185 · Ca3Fe2Si3O12'], score:70, validation:{metrics:{rwp_percent:25}}, reason:'增加残差峰对应候选', converged:false };
  const host = mount({ status:'completed', run_id:'cursor-run', source_match_run_id:'search-42', trials:[first,second], incumbent:first, pywpem_review:{status:'completed', reviews:[{full:{status:'completed',candidate_ids:second.candidate_ids,labels:second.labels,metrics:{rwp_percent:32.3},converged:false},removals:[]}]}});
  function Probe() { const shared = useMultiphaseCanvas('match-42'); const selected = resolveMultiphaseSelection(shared.state, shared.selection); return <output aria-label="同步选择">{shared.active ? `${selected?.lane}:${selected?.trialId ?? selected?.reviewIndex}` : 'inactive'}</output>; }
  render(<Probe/>);
  await openMode();
  await waitFor(() => expect(screen.getByLabelText('同步选择').textContent).toBe('agent:cursor-first'));
  const bar = screen.getByRole('button', {name:/第 2 轮 · COD 1537407/});
  fireEvent.mouseEnter(bar);
  const bubble = screen.getByRole('tooltip', {name:'组合评估信息'});
  expect(bubble.textContent).toContain('COD 9001185');
  expect(bubble.textContent).toContain('搜索得分：70.000 / 100');
  expect(bubble.textContent).toContain('留出 Rwp：25.000%');
  expect(bubble.textContent).toContain('未达到判据');
  fireEvent.click(bar);
  await waitFor(() => expect(screen.getByLabelText('同步选择').textContent).toBe('agent:cursor-second'));
  fireEvent.keyDown(bar,{key:'ArrowLeft'});
  await waitFor(() => expect(screen.getByLabelText('同步选择').textContent).toBe('agent:cursor-first'));
  fireEvent.click(screen.getByRole('button',{name:'04 联合复核'}));
  await waitFor(() => expect(screen.getByLabelText('同步选择').textContent).toBe('pywpem:0'));
  fireEvent.mouseEnter(screen.getByRole('button',{name:/复核 1 · COD 1537407/}));
  expect(screen.getByRole('tooltip', {name:'组合评估信息'}).textContent).toContain('全谱 Rwp：32.300%');
  expect(screen.getByRole('tooltip', {name:'组合评估信息'}).textContent).not.toContain('搜索得分');
  expect(host.startMultiphase).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'02 匹配结果'}));
  await waitFor(() => expect(screen.getByLabelText('同步选择').textContent).toBe('inactive'));
});

it('opens a results conversation through the host without triggering analysis', async () => {
  const openResultsConversation = vi.fn().mockResolvedValue({});
  const host = mount({status:'idle'}, {openResultsConversation});
  const discussion = await screen.findByRole('button',{name:'结果讨论'});
  expect(discussion.previousElementSibling).toBe(screen.getByRole('button',{name:'运行历史'}));
  fireEvent.click(discussion);
  await waitFor(() => expect(openResultsConversation).toHaveBeenCalledOnce());
  expect(host.startMultiphase).not.toHaveBeenCalled();
});

it('preserves the branch and pinned trial when its Legion workspace tab remounts', async () => {
  const second = {...completeTrial, trial_id:'pinned-2', iteration:2, score:70, labels:['Pinned combination']};
  const state:XrdMultiphaseState = {status:'completed',run_id:'remount-run',source_match_run_id:'search-42',trials:[completeTrial,second],incumbent:completeTrial};
  mount(state);
  await openMode();
  fireEvent.click(screen.getByRole('button',{name:/第 2 轮 · Pinned combination/}));
  cleanup();
  const host = mount(state);
  await screen.findByRole('region',{name:'多相自动筛选与联合拟合'});
  expect(screen.getByRole('button',{name:/第 2 轮 · Pinned combination/}).getAttribute('aria-pressed')).toBe('true');
  expect(screen.getByRole('button',{name:'03 组合筛选'}).getAttribute('aria-current')).toBe('step');
  expect(host.startMultiphase).not.toHaveBeenCalled();
});


it('separates advancing to review from restarting with updated options', () => {
  const advance = vi.fn(), restart = vi.fn(), start = vi.fn();
  render(<MultiphasePanel {...panelActions} state={{ status: 'completed', run_id: 'run-1', source_match_run_id: 'search-42', incumbent: completeTrial }} currentMatchRunId="search-42" view="search" onAdvance={advance} onRestart={restart} onStart={start}/>);
  fireEvent.click(screen.getByRole('button', { name: '下一步：联合复核' }));
  expect(advance).toHaveBeenCalledOnce();
  expect(start).not.toHaveBeenCalled();
  expect(restart).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '重新搜索' }));
  expect(restart).toHaveBeenCalledOnce();
});


it('keeps manual review navigation separate from execution', () => {
  const review = vi.fn(), back = vi.fn();
  render(<MultiphasePanel {...panelActions} state={{status:'completed',run_id:'m',source_match_run_id:'s',incumbent:completeTrial,trials:[completeTrial],review_recommendations:{status:'completed',combinations:[completeTrial.candidate_ids]},pywpem_review:{status:'pending'}}} currentMatchRunId="s" view="review" onReview={review} onBack={back} onAdvance={vi.fn()}/>);
  expect(review).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'开始联合复核'}));
  expect(review).toHaveBeenCalledOnce();
  expect(screen.queryByRole('button',{name:'已到流程末步'})).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:'返回组合筛选'}));
  expect(back).toHaveBeenCalledOnce();
});


it('hides BO selector and comparison when unchecked without deleting its history',()=>{
 const state = {status:'completed',run_id:'m',trials:[completeTrial],incumbent:completeTrial,baseline:{status:'completed',trials:[completeTrial],incumbent:completeTrial}};
 const {rerender}=render(<MultiphasePanel {...panelActions} state={state} options={{evaluate_baseline:false}}/>);
 expect(screen.queryByRole('button',{name:'BO_baseline'})).toBeNull();
 expect(screen.queryByRole('table',{name:'多相优化算法比较'})).toBeNull();
 rerender(<MultiphasePanel {...panelActions} state={state} options={{evaluate_baseline:true}}/>);
 expect(screen.getByRole('button',{name:'BO_baseline'})).toBeTruthy();
});

it('allows changing the optional BO setting for the next run while busy',()=>{
 const change=vi.fn();
 render(<MultiphasePanel {...panelActions} state={{status:'running'}} onOptions={change}/>);
 fireEvent.click(screen.getByRole('button',{name:'下次运行设置'}));
 const toggle=screen.getByRole('checkbox',{name:'同拟合预算评测 BO_baseline'});
 expect((toggle as HTMLInputElement).disabled).toBe(false);
 fireEvent.click(toggle);
 expect(change).toHaveBeenCalledWith(expect.objectContaining({evaluate_baseline:false}));
});


it('lets users supplement Jev recommendations and submits only checked combinations', () => {
 const review = vi.fn();
 const extra = {...completeTrial, trial_id:'extra', candidate_ids:['extra'], labels:['extra phase']};
 render(<MultiphasePanel {...panelActions} state={{status:'completed',run_id:'m',trials:[completeTrial,extra],incumbent:completeTrial,review_recommendations:{status:'completed',combinations:[completeTrial.candidate_ids]}}} view="review" onReview={review}/>);
 expect(review).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('checkbox',{name:/extra phase/}));
 fireEvent.click(screen.getByRole('button',{name:'开始联合复核'}));
 expect(review).toHaveBeenLastCalledWith([completeTrial.candidate_ids,['extra']]);
});


it('restores and explicitly saves next-run settings without starting a search', async () => {
 const save = vi.fn().mockResolvedValue(undefined);
 const host = mount({status:'completed',run_id:'m',next_options:{budget:55,max_phases:3,evaluate_baseline:false}}, {saveMultiphaseOptions:save});
 await openMode();
 const budget = await screen.findByRole('spinbutton',{name:'每个算法的评估预算'});
 expect((budget as HTMLInputElement).value).toBe('55');
 fireEvent.change(budget,{target:{value:'60'}});
 fireEvent.click(screen.getByRole('button',{name:'保存设置'}));
 await screen.findByText('已保存，下次搜索生效');
 expect(save).toHaveBeenCalledWith(expect.objectContaining({budget:60,evaluate_baseline:false}));
 expect(host.startMultiphase).not.toHaveBeenCalled();
});

it('hides selection and search exclusions after review starts',()=>{
 render(<MultiphasePanel {...panelActions} state={{status:'running',run_id:'review-clean',trials:[completeTrial],incumbent:completeTrial,pywpem_review:{status:'running',selected_combinations:[completeTrial.candidate_ids]},excluded_candidates:[{candidate_id:'excluded',input_error:'missing'}]}} view="review"/>);
 expect(screen.queryByRole('group',{name:/待复核组合/})).toBeNull();
 expect(screen.queryByText('PyWPEM 联合复核')).toBeNull();
 expect(screen.queryByText('正在联合拟合，实时谱线同步到画布。')).toBeNull();
 expect(screen.queryByText(/未进入组合评估的候选/)).toBeNull();
});


it('keeps history immediately before discussion at the end of joint review', async () => {
  mount({status:'completed',run_id:'finished-review',source_match_run_id:'search-42',pywpem_review:{status:'completed',reviews:[]}}, {openResultsConversation:vi.fn()});
  fireEvent.click(await screen.findByRole('button',{name:'04 联合复核'}));
  const discussion = await screen.findByRole('button',{name:'结果讨论'});
  expect(discussion.classList.contains('xrd-results-discussion-final')).toBe(true);
  expect(discussion.previousElementSibling).toBe(screen.getByRole('button',{name:'运行历史'}));
  expect(discussion.nextElementSibling).toBeNull();
  expect(screen.getAllByRole('button',{name:'运行历史'})).toHaveLength(1);
});
