// @vitest-environment jsdom
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { PluginViewProps } from '../plugins/sdk';
import { AuthoringCardView } from './AuthoringCardView';
import { faceStudio } from './faceDesign';
import type { CardInterface } from './projectTypes';

const face: CardInterface['face'] = { title: 'Analyzer', description: '', variant: 'text', tone: 'sage', color: '#617b72', icon: 'calculator', finish: 'normal', layout: 'stack', help_text: '', button_label: 'Analyze' };
const design: CardInterface = { face: { ...face, studio: faceStudio(face) }, function: { fields: [{ key: 'count', label: 'Count', type: 'number', default: 2, required: true }], operation: 'sum', template: '', separator: '' }, action: 'analyze_structure', result_key: 'result', surfaces: ['body'] };
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('routes the designed primary action through the installed host with actual config defaults', async () => {
  const action = vi.fn().mockResolvedValue({ result: { volume: 42 } });
  const props = { card: { id: 'analyzer', status: 'idle', config: { count: 7 } }, definition: { authoring: design }, level: 'inspector', host: { resourceAction: action } } as unknown as PluginViewProps;
  const screen = render(<AuthoringCardView {...props} />);
  expect((screen.getByLabelText('Count *') as HTMLInputElement).value).toBe('7');
  fireEvent.click(screen.getByRole('button', { name: 'Analyze' }));
  await screen.findByText(/"volume": 42/);
  expect(action).toHaveBeenCalledOnce();
  expect(action).toHaveBeenCalledWith('analyze_structure', { count: 7 });
});

it('binds a custom button to its explicit action and displays authorization failures without retrying with confirmation', async () => {
  const linked = structuredClone(design), surface = linked.face.studio!.modes.inspector!;
  surface.elements.push({ id: 'export', kind: 'button', text: 'Export result', button: { action: 'custom' }, x: 28, y: 500, width: 200, height: 35, font_size: 14, color: '#111111', align: 'left' });
  linked.function.button_bindings = [{ mode: 'inspector', element_id: 'export', logic_id: 'export_result' }];
  const action = vi.fn().mockRejectedValue(new Error('This action requires approval.'));
  const screen = render(<AuthoringCardView {...{ card: { id: 'analyzer', status: 'idle', config: {} }, definition: { authoring: linked }, level: 'inspector', host: { resourceAction: action } } as unknown as PluginViewProps} />);
  fireEvent.click(screen.getByRole('button', { name: 'Export result' }));
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('requires approval'));
  expect(action).toHaveBeenCalledOnce();
  expect(action).toHaveBeenCalledWith('export_result', { count: 2 });
});
