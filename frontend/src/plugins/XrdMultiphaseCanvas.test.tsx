// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { FrameCanvas, setParameterMode } from '../../../plugins/xrd/frontend/FrameCanvas';
import { publishMultiphaseState, resolveMultiphaseSelection, retainMultiphasePublisher, setMultiphaseSelection, useMultiphaseCanvas, useMultiphaseCanvasPolling } from '../../../plugins/xrd/frontend/MultiphaseCanvasState';
import type { PluginViewProps, XrdMultiphaseState, XrdMultiphaseTrial } from './sdk';

vi.mock('../../../plugins/xrd/frontend/CandidateStructure', () => ({ StructureCanvas: ({ structure }: { structure: { filename: string } }) => <div data-testid="rendered-cif">{structure.filename}</div> }));
afterEach(() => { cleanup(); vi.useRealTimers(); });
const plot = { observed: [[10, 3], [20, 6]], calculated: [[10, 2], [20, 5]], contributions: [
  { candidate_id: 'a', label: 'Alpha', points: [[10, 1], [20, 2]] },
  { candidate_id: 'b', label: 'Beta', points: [[10, 1], [20, 3]] },
] };
const trial = (id: string, iteration: number, extra: Partial<XrdMultiphaseTrial> = {}): XrdMultiphaseTrial => ({
  trial_id: id, iteration, candidate_ids: ['a', 'b'], labels: ['Alpha', 'Beta'], status: 'completed', score: 60, plot,
  structures: ['a', 'b'].map(candidate_id => ({ candidate_id, cif: { filename: `${id}-${candidate_id}.cif`, source_base64: 'Y2lm' }, source_kind: 'profile_cell_fit' })), ...extra,
});
const initial = (id: string): XrdMultiphaseState => ({ status: 'running', run_id: id, source_match_run_id: 'match', trials: [trial('one', 1)], incumbent: trial('one', 1) });
const host = { getAgentInfo: vi.fn(async () => ({ details: {} })), getInputs: vi.fn(async () => []) } as unknown as PluginViewProps['host'];
function Canvases({ source }: { source: string }) {
  return <>{['spectrum', 'structure'].map(type => <div data-testid={`${type}-canvas`} key={type}><FrameCanvas card={{ id: `${source}-${type}`, type: `xrd.${type}-canvas`, config: { source_node_id: source } } as unknown as PluginViewProps['card']} definition={{} as PluginViewProps['definition']} host={host} level="workspace"/></div>)}</>;
}

it('shares selected phase between linked canvases while retaining the joint spectrum', () => {
  const source = 'multi-canvas-phase'; setParameterMode(source, false); publishMultiphaseState(source, initial('run-phase'), true, 'match');
  const { container } = render(<Canvases source={source}/>);
  expect(screen.getByTestId('rendered-cif').textContent).toBe('one-a.cif');
  expect(container.querySelectorAll('polyline[data-phase-id]')).toHaveLength(2);
  fireEvent.change(screen.getByRole('combobox', { name: '谱画布物相' }), { target: { value: 'b' } });
  expect(screen.getByTestId('rendered-cif').textContent).toBe('one-b.cif');
  expect((screen.getByRole('combobox', { name: '结构画布物相' }) as HTMLSelectElement).value).toBe('b');
  expect(container.querySelectorAll('polyline[data-phase-id]')).toHaveLength(1);
  expect(within(screen.getByTestId('spectrum-canvas')).getByText(/联合计算谱/)).toBeTruthy();
});

it('follows new completed trials, pins selected bars, and clears missing or failed CIF instead of reusing old structure', () => {
  const source = 'multi-canvas-follow'; const first = initial('run-follow');
  setParameterMode(source, false); publishMultiphaseState(source, first, true, 'match');
  render(<Canvases source={source}/>);
  act(() => publishMultiphaseState(source, { ...first, trials: [...first.trials!, trial('two', 2)] }, true, 'match'));
  expect(screen.getByTestId('rendered-cif').textContent).toBe('two-a.cif');
  act(() => setMultiphaseSelection(source, { lane: 'agent', trialId: 'one', follow: false }));
  act(() => publishMultiphaseState(source, { ...first, trials: [...first.trials!, trial('two', 2), trial('three', 3)] }, true, 'match'));
  expect(screen.getByTestId('rendered-cif').textContent).toBe('one-a.cif');
  act(() => publishMultiphaseState(source, { ...first, trials: [trial('one', 1, { structures: [] })] }, true, 'match'));
  expect(screen.queryByTestId('rendered-cif')).toBeNull();
  expect(screen.getByText(/尚未提供这个物相的 CIF/)).toBeTruthy();
  act(() => publishMultiphaseState(source, { ...first, trials: [trial('one', 1, { status: 'failed', error: 'fit stopped' })] }, true, 'match'));
  expect(screen.queryByTestId('rendered-cif')).toBeNull();
  expect(screen.queryByRole('img', { name: '同步 XRD 谱画布' })).toBeNull();
});

it('clears old-run and old-match cursor and does not leak multi structures into the single branch', () => {
  const source = 'multi-canvas-reset'; const state = initial('old-run');
  setParameterMode(source, false); publishMultiphaseState(source, state, true, 'match');
  render(<Canvases source={source}/>);
  fireEvent.change(screen.getByRole('combobox', { name: '结构画布物相' }), { target: { value: 'b' } });
  act(() => publishMultiphaseState(source, { ...state, run_id: 'new-run', trials: [] }, true, 'match'));
  expect(screen.queryByTestId('rendered-cif')).toBeNull();
  act(() => publishMultiphaseState(source, state, true, 'new-match'));
  expect(screen.queryByTestId('rendered-cif')).toBeNull();
  expect(screen.getAllByText(/先前多相运行属于其他检索/)).toHaveLength(2);
  act(() => publishMultiphaseState(source, state, false, 'match'));
  expect(screen.queryByRole('combobox', { name: '结构画布物相' })).toBeNull();
  expect(screen.queryByTestId('rendered-cif')).toBeNull();
});

it('uses exact PyWPEM review/live structures and preserves explicit search scope', () => {
  const state: XrdMultiphaseState = { ...initial('review-run'), status: 'completed', pywpem_review: {
    status: 'completed', reviews: [{ full: { ...trial('py', 8), metrics: { rwp_percent: 32 }, structures: [{ candidate_id: 'a', source_kind: 'pywpem_cell_fit', cif: { filename: 'pywpem-fitted.cif', source_base64: 'Y2lm' } }] }, removals: [] }],
  } };
  expect(resolveMultiphaseSelection(state)?.structures?.[0].cif?.filename).toBe('pywpem-fitted.cif');
  expect(resolveMultiphaseSelection(state, { lane: 'agent', follow: true, scope: 'search' })?.trialId).toBe('one');
  const live = { stage: 'pywpem' as const, trial_id: 'live', iteration: 9, candidate_ids: ['b'], status: 'running', plot, structures: [{ candidate_id: 'b', source_kind: 'pywpem_cell_fit' as const, cif: { filename: 'live-b.cif', source_base64: 'Y2lm' } }] };
  expect(resolveMultiphaseSelection({ ...state, pywpem_review: { ...state.pywpem_review!, status: 'running', live } })?.structures?.[0].cif?.filename).toBe('live-b.cif');
  expect(resolveMultiphaseSelection({ ...state, pywpem_review: undefined }, { lane: 'pywpem', follow: true, scope: 'review' })).toBeUndefined();
});

it('resets an explicitly selected phase at a new run boundary', () => {
  const source='phase-reset-observer'; const state=initial('first');
  setParameterMode(source,false);publishMultiphaseState(source,state,true,'match');
  function Observer(){const snapshot=useMultiphaseCanvas(source);return <span data-testid="cursor-phase">{snapshot.phaseId??'all'}</span>;}
  render(<><Canvases source={source}/><Observer/></>);
  fireEvent.change(screen.getByRole('combobox',{name:'结构画布物相'}),{target:{value:'b'}});
  expect(screen.getByTestId('cursor-phase').textContent).toBe('b');
  act(()=>publishMultiphaseState(source,{...state,run_id:'second'},true,'match'));
  expect(screen.getByTestId('cursor-phase').textContent).toBe('all');
});

it('keeps one live poll per source while the analysis tab is hidden and pauses when its publisher returns', async () => {
  vi.useFakeTimers();
  const source='multi-poll-hidden', state=initial('poll-run');
  publishMultiphaseState(source,state,true,'match');
  const read=vi.fn(async()=>({...state,trials:[trial('two',2)]}));
  function Subscriber(){useMultiphaseCanvasPolling(source,read);const snapshot=useMultiphaseCanvas(source);return <span>{resolveMultiphaseSelection(snapshot.state,snapshot.selection)?.trialId}</span>;}
  const release=retainMultiphasePublisher(source);
  const {unmount}=render(<><Subscriber/><Subscriber/></>);
  await act(async()=>{await vi.advanceTimersByTimeAsync(2500);});
  expect(read).not.toHaveBeenCalled();
  release();
  await act(async()=>{await vi.advanceTimersByTimeAsync(2500);});
  expect(read).toHaveBeenCalledTimes(1);
  expect(screen.getAllByText('two')).toHaveLength(2);
  const releaseAgain=retainMultiphasePublisher(source);
  await act(async()=>{await vi.advanceTimersByTimeAsync(2500);});
  expect(read).toHaveBeenCalledTimes(1);
  unmount();releaseAgain();
  await act(async()=>{await vi.advanceTimersByTimeAsync(5000);});
  expect(read).toHaveBeenCalledTimes(1);
});

it('preserves a phase chosen on the structure canvas across repeated terminal-result publications', async () => {
  vi.useFakeTimers();
  const source='phase-terminal-polls';
  const state:XrdMultiphaseState={...initial('terminal-review'),status:'completed',pywpem_review:{status:'completed',reviews:[{full:trial('pywpem',20),removals:[]}]}};
  setParameterMode(source,false);publishMultiphaseState(source,state,true,'match');
  setMultiphaseSelection(source,{lane:'pywpem',scope:'review',follow:true});
  render(<Canvases source={source}/>);
  fireEvent.change(screen.getByRole('combobox',{name:'结构画布物相'}),{target:{value:'b'}});
  const interval=setInterval(()=>publishMultiphaseState(source,structuredClone(state),true,'match'),5000);
  try {
    await act(async()=>{await vi.advanceTimersByTimeAsync(15000);});
    expect((screen.getByRole('combobox',{name:'结构画布物相'}) as HTMLSelectElement).value).toBe('b');
    expect((screen.getByRole('combobox',{name:'谱画布物相'}) as HTMLSelectElement).value).toBe('b');
    expect(screen.getByTestId('rendered-cif').textContent).toBe('pywpem-b.cif');
  } finally {clearInterval(interval);}
});

it('shows the failed review and its error instead of waiting forever, retaining search results', () => {
  const state: XrdMultiphaseState = { ...initial('failed-review'), status: 'completed', pywpem_review: {
    status: 'failed', reviews: [{ full: { candidate_ids: ['a'], status: 'failed', error: 'Cannot convert complex to float' }, removals: [] }],
  } };
  const display = resolveMultiphaseSelection(state, { lane: 'pywpem', follow: true, scope: 'review' });
  expect(display?.status).toBe('failed');
  expect(display?.error).toBe('Cannot convert complex to float');
  expect(display?.reviewIndex).toBe(0);
  expect(resolveMultiphaseSelection(state, { lane: 'agent', follow: true, scope: 'search' })?.trialId).toBe('one');
});

it('does not fetch results or mount a structure renderer for a canvas preview',()=>{
 const getAgentInfo=vi.fn(),getMultiphase=vi.fn();
 render(<FrameCanvas card={{id:'light',type:'xrd.structure-canvas',config:{source_node_id:'owner'}} as unknown as PluginViewProps['card']} definition={{} as PluginViewProps['definition']} host={{getAgentInfo,getMultiphase} as unknown as PluginViewProps['host']} level="preview"/>);
 expect(screen.getByText('打开工作区查看')).toBeTruthy();
 expect(getAgentInfo).not.toHaveBeenCalled();expect(getMultiphase).not.toHaveBeenCalled();
 expect(screen.queryByTestId('rendered-cif')).toBeNull();
});

it('loads only the selected summary frame when opening the working canvas',async()=>{
 const source='lazy-detail-canvas';setParameterMode(source,false);
 const summary={...trial('selected',1),plot:undefined,structures:undefined};
 publishMultiphaseState(source,{status:'completed',run_id:'lazy-run',source_match_run_id:'match',trials:[summary],incumbent:summary},true,'match');
 const getMultiphaseFrame=vi.fn().mockResolvedValue({plot,structures:trial('selected',1).structures});
 render(<FrameCanvas card={{id:'lazy-structure',type:'xrd.structure-canvas',config:{source_node_id:source}} as unknown as PluginViewProps['card']} host={{...host,getMultiphaseFrame}} definition={{} as PluginViewProps['definition']} level="workspace"/>);
 await waitFor(()=>expect(screen.getByTestId('rendered-cif').textContent).toBe('selected-a.cif'));
 expect(getMultiphaseFrame).toHaveBeenCalledWith({run_id:'lazy-run',lane:'agent',trial_id:'selected',review_index:undefined});
});
