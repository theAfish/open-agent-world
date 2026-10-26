// @vitest-environment jsdom
import { useState } from 'react';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { PackVisualEditor, type PackVisualEditorProps } from './PackVisualEditor';
import { faceStudio } from './faceDesign';
import type { CardInterface } from './projectTypes';
import type { NodeSurfaceLevel } from '../types/world';

afterEach(cleanup);
const face: CardInterface['face'] = { title: 'Analyzer', description: 'An authored card', variant: 'text', tone: 'sage', color: '#617b72', icon: 'calculator', finish: 'normal', layout: 'stack', help_text: '', button_label: 'Analyze' };
function initial(): CardInterface {
  return { face: { ...face, studio: faceStudio(face) }, function: { fields: [{ key: 'count', label: 'Count', type: 'number', default: 2, required: true }], operation: 'sum', template: '', separator: '' }, action: 'analyze_structure', result_key: 'result', surfaces: ['body'] };
}
function Editor(props: Partial<PackVisualEditorProps> = {}) {
  const [design, setDesign] = useState(props.design ?? initial());
  const [level, setLevel] = useState<NodeSurfaceLevel>('inspector');
  return <><PackVisualEditor mode="design" {...props} design={design} onChange={setDesign} level={level} onLevelChange={setLevel} />
    <output data-testid="draft">{JSON.stringify(design)}</output></>;
}
const draft = (): CardInterface => JSON.parse(screen.getByTestId('draft').textContent!);

it('selects the actual artwork to edit text and geometry without rewriting another surface', () => {
  const original = initial();
  render(<Editor design={original} />);
  const canvas = screen.getByRole('region', { name: 'Card canvas' });
  fireEvent.click(within(canvas).getByRole('button', { name: 'Select Title' }));
  fireEvent.change(screen.getByLabelText('Text'), { target: { value: 'Cell analyzer' } });
  fireEvent.change(screen.getByLabelText('X'), { target: { value: '43' } });
  const title = draft().face.studio!.modes.inspector!.elements.find(element => element.kind === 'title')!;
  expect(draft().face.title).toBe('Cell analyzer');
  expect(title.x).toBe(43);
  expect(draft().face.studio!.modes.preview).toEqual(original.face.studio!.modes.preview);
  fireEvent.keyDown(within(canvas).getByRole('button', { name: 'Select Title' }), { key: 'ArrowRight', shiftKey: true });
  expect(draft().face.studio!.modes.inspector!.elements.find(element => element.kind === 'title')!.x).toBe(53);
});

it('adds an editable field and a real button binding while retaining bindings on other surfaces', () => {
  const original = initial();
  original.function.button_bindings = [{ mode: 'workspace', element_id: 'export', logic_id: 'export_result' }];
  render(<Editor design={original} />);
  fireEvent.click(screen.getByRole('button', { name: 'Add field' }));
  fireEvent.change(screen.getByLabelText('Field label'), { target: { value: 'Multiplier' } });
  fireEvent.change(screen.getByLabelText('Field type'), { target: { value: 'number' } });
  expect(draft().function.fields[1]).toMatchObject({ label: 'Multiplier', type: 'number', default: 0 });
  expect(screen.getByRole('button', { name: 'Select Multiplier' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Add button' }));
  const added = draft().face.studio!.modes.inspector!.elements.at(-1)!;
  expect(added).toMatchObject({ kind: 'button', button: { action: 'custom' } });
  expect(draft().function.button_bindings).toContainEqual({ mode: 'inspector', element_id: added.id, logic_id: 'analyze_structure' });
  fireEvent.click(screen.getByRole('button', { name: 'Remove element' }));
  expect(draft().function.button_bindings).toEqual(original.function.button_bindings);
});

it('binds a selected Python button and tests the actual callback with edited inputs and surfaced errors', async () => {
  const run = vi.fn().mockRejectedValue(new Error('The installed action requires approval.'));
  const original = initial();
  original.face.studio!.modes.inspector!.elements.push({ id: 'export', kind: 'button', button: { action: 'custom' }, text: 'Export', x: 28, y: 510, width: 180, height: 36, font_size: 14, color: '#111111', align: 'left' });
  original.face.studio!.modes.inspector!.elements.push({ id: 'open', kind: 'button', button: { action: 'open' }, text: 'Expand', x: 230, y: 510, width: 180, height: 36, font_size: 14, color: '#111111', align: 'left' });
  original.function.button_bindings = [{ mode: 'inspector', element_id: 'export', logic_id: 'export_result' }];
  render(<Editor mode="function" cardKind="python" run={run} design={original} actions={[{ id: 'analyze_structure' }, { id: 'export_result' }]} />);
  fireEvent.click(screen.getByRole('button', { name: 'Select Export' }));
  expect((screen.getByLabelText('Bound action') as HTMLSelectElement).value).toBe('export_result');
  expect(screen.queryByLabelText('Operation')).toBeNull();
  fireEvent.change(screen.getByLabelText('Count *'), { target: { value: '7' } });
  fireEvent.click(screen.getByRole('button', { name: 'Test action' }));
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('requires approval'));
  expect(run).toHaveBeenCalledOnce();
  expect(run).toHaveBeenCalledWith('export_result', { count: 7 });
  fireEvent.change(screen.getByLabelText('Bound action'), { target: { value: 'analyze_structure' } });
  expect(draft().function.button_bindings).toEqual([{ mode: 'inspector', element_id: 'export', logic_id: 'analyze_structure' }]);
  fireEvent.click(screen.getByRole('button', { name: 'Select Expand' }));
  expect((screen.getByRole('button', { name: 'Test action' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Test action' }));
  expect(run).toHaveBeenCalledOnce();
});

it('keeps selection when switching Design and Function and shows actual test output on the card', async () => {
  const run = vi.fn().mockResolvedValue({ total: 12 });
  function Modes() {
    const [mode, setMode] = useState<'design' | 'function'>('design');
    return <><button onClick={() => setMode(value => value === 'design' ? 'function' : 'design')}>Switch mode</button><Editor mode={mode} run={run} /></>;
  }
  render(<Modes />);
  fireEvent.click(screen.getByRole('button', { name: 'Select Analyze' }));
  fireEvent.click(screen.getByRole('button', { name: 'Switch mode' }));
  expect(screen.getByRole('button', { name: 'Select Analyze' }).getAttribute('aria-pressed')).toBe('true');
  fireEvent.change(screen.getByLabelText('Operation'), { target: { value: 'multiply' } });
  expect(draft().function.operation).toBe('multiply');
  fireEvent.change(screen.getByLabelText('Count *'), { target: { value: '7' } });
  const canvas = screen.getByRole('region', { name: 'Card canvas' });
  expect(within(canvas).getByText('7')).toBeTruthy();
  expect(draft().function.fields[0].default).toBe(2);
  fireEvent.click(screen.getByRole('button', { name: 'Switch mode' }));
  expect(within(canvas).getByText('2')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Switch mode' }));
  expect(within(canvas).getByText('7')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Test action' }));
  await waitFor(() => expect(screen.getByLabelText('Action test result').textContent).toContain('"total": 12'));
  expect(run).toHaveBeenCalledWith('analyze_structure', { count: 7 });
  expect(within(screen.getByRole('region', { name: 'Card canvas' })).getByText(/"total": 12/)).toBeTruthy();
});

it('enables a visual surface explicitly while preserving other custom surface choices', () => {
  const original = initial();
  original.surfaces = ['preview', 'settings'];
  render(<Editor design={original} cardKind="python" />);
  fireEvent.change(screen.getByLabelText('Text'), { target: { value: 'Edited presentation' } });
  expect(draft().surfaces).toEqual(['preview', 'settings']);
  fireEvent.click(screen.getByLabelText('Use visual design on this surface'));
  expect(draft().surfaces).toEqual(['preview', 'settings', 'body']);
  fireEvent.click(screen.getByLabelText('Use visual design on this surface'));
  expect(draft().surfaces).toEqual(original.surfaces);
});
