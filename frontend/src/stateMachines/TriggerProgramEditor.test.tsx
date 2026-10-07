// @vitest-environment jsdom
import { useState } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { worldApi } from '../api/client';
import { useLocale } from '../i18n';
import { useWorldStore } from '../state/worldStore';
import type { PluginCatalog, WorldCard } from '../types/world';
import { createProgram, parseExpression, printExpression } from './expressions';
import { TriggerProgramEditor } from './TriggerProgramEditor';
import type { StateMachine, TriggerProgram } from './model';

const machine: StateMachine = { version: 2, entities: [
  { id: 'agent', card_id: 'live-agent', label: 'Agent', kind: 'card', states: [{ id: 'idle', label: 'Idle', position: { x: 0, y: 0 } }], initial_state: 'idle' },
  { id: 'group', card_id: 'legion', label: 'Legion', kind: 'group', states: [{ id: 'idle', label: 'Idle', position: { x: 0, y: 0 } }], initial_state: 'idle' },
], rules: [] };
const cards = [{ id: 'live-agent', name: 'Agent', type: 'agent' }, { id: 'legion', name: 'Legion', type: 'legion' }] as WorldCard[];
const catalog = { node_types: [{ id: 'agent', traits: ['core.agent'] }, { id: 'legion', traits: [] }], plugins: [], packs: [], relationships: [] } as unknown as PluginCatalog;
const initial = () => createProgram({ entity_id: 'agent', event: 'run.completed' });

function Harness({ start = initial(), valid = vi.fn() }: { start?: TriggerProgram; valid?: (value: boolean) => void }) {
  const [program, setProgram] = useState(start);
  return <><TriggerProgramEditor program={program} machine={machine} cards={cards} catalog={catalog} onChange={setProgram} onValidityChange={valid} /><output data-testid="saved">{JSON.stringify(program)}</output></>;
}
const saved = () => JSON.parse(screen.getByTestId('saved').textContent!) as TriggerProgram;
const formula = () => screen.getByLabelText('Trigger formula') as HTMLTextAreaElement;

beforeEach(() => {
  useLocale.setState({ locale: 'en' });
  useWorldStore.setState({ edges: [] });
  vi.spyOn(worldApi, 'getStateMachineEvents').mockImplementation(async agentId => ({ events: [
    { key: 'run.completed', label: 'Run completed', category: 'run', outcome: 'succeeded', runtime_bound: false },
    { key: 'run.failed', label: 'Run failed', category: 'run', outcome: 'failed', runtime_bound: false },
    { key: 'capability.succeeded', label: 'Capability succeeded', category: 'capability', outcome: 'succeeded', runtime_bound: false },
    { key: 'agent.summoned', label: 'Agent summoned', category: 'lifecycle', outcome: 'summoned', runtime_bound: false },
    { key: 'custom', label: 'Custom event', category: 'custom', outcome: 'custom', runtime_bound: false },
  ], operations: [], sources: agentId === 'live-agent' ? [{ id: 'host:run', label: 'Agent Run', capability: null, target_card_id: 'live-agent', target_name: 'Agent', default_event: 'run.completed', events: [{key:'run.completed',label:'Run completed', category:'run',outcome:'succeeded', runtime_bound:true}] }, { id: 'capability:document.write:document', capability: 'document.write', label: 'Write a document',
    target_card_id: 'document', target_name: 'Report', default_event: 'capability.succeeded',
    events: [{ key: 'capability.succeeded', label: 'When the call succeeds', category: 'capability', outcome: 'succeeded', runtime_bound: true }] }] : [] }));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('trigger program editor', () => {
  it('keeps a single-object rule focused on the action and hides technical controls', async () => {
    render(<TriggerProgramEditor program={initial()} machine={{ ...machine, entities: [machine.entities[0]] }} cards={cards} catalog={catalog} onChange={vi.fn()} />);
    await screen.findByRole('option', { name: 'Agent Run \u00b7 Agent' });
    expect(screen.getByLabelText('Trigger / interface')).toBeTruthy();
    expect(screen.queryByLabelText('Who')).toBeNull();
    expect(screen.queryByLabelText('Trigger formula')).toBeNull();
    expect(screen.queryByLabelText('Counting window')).toBeNull();
    expect(screen.queryByLabelText('Signal name A')).toBeNull();
    expect(screen.queryByRole('option', { name: 'Custom event' })).toBeNull();
    expect(screen.queryByRole('option', { name: 'Agent is summoned' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Advanced conditions' }));
    expect(screen.getByLabelText('Trigger formula')).toBeTruthy();
    expect(screen.queryByRole('option', { name: 'Custom event' })).toBeNull();
    expect(screen.queryByRole('option', { name: 'Agent is summoned' })).toBeNull();
  });

  it('preserves saved custom conditions without showing formulas until requested', async () => {
    const valid = vi.fn();
    const start = { ...createProgram({ entity_id: 'agent', event: 'review.approved' }), expression: parseExpression('count(A) % 3 == 0') };
    render(<Harness start={start} valid={valid} />);
    await screen.findByRole('option', { name: 'Agent Run \u00b7 Agent' });
    expect(screen.getByRole('option', { name: 'Custom conditions' })).toBeTruthy();
    expect(screen.queryByLabelText('Custom event key')).toBeNull();
    expect(screen.queryByLabelText('Trigger formula')).toBeNull();
    expect(saved()).toEqual(start);
    fireEvent.click(screen.getByRole('button', { name: 'Advanced conditions' }));
    expect(screen.queryByLabelText('Custom event key')).toBeNull();
    expect(saved().signals[0].match.event).toBe('review.approved');
    expect(formula().value).toBe('count(A) % 3 == 0');
    fireEvent.change(formula(), { target: { value: 'count(A) >' } });
    expect(valid).toHaveBeenLastCalledWith(false);
    expect((screen.getByRole('button', { name: 'Advanced conditions' }) as HTMLButtonElement).disabled).toBe(true);
    expect(saved()).toEqual(start);
  });


  it('accepts an external expression replacement without writing the old draft back', async () => {
    const change = vi.fn();
    const program = initial();
    const view = render(<TriggerProgramEditor program={program} machine={machine} cards={cards} catalog={catalog} onChange={change} />);
    await screen.findByRole('option', { name: 'Agent Run \u00b7 Agent' });
    const replacement = { ...program, expression: parseExpression('count(A) >= 7') };
    view.rerender(<TriggerProgramEditor program={replacement} machine={machine} cards={cards} catalog={catalog} onChange={change} />);
    expect((screen.getByLabelText('Times') as HTMLInputElement).value).toBe('7');
    expect(change).not.toHaveBeenCalled();
  });

  it('keeps invalid formula drafts while editing signals and applies the repaired expression', async () => {
    const valid = vi.fn();
    render(<Harness valid={valid} />);
    await screen.findByRole('option', { name: 'Agent Run \u00b7 Agent' });
    fireEvent.change(screen.getByLabelText('Trigger behavior'), { target: { value: 'all' } });
    expect(saved().signals[0].match.event).not.toBe(saved().signals[1].match.event);
    expect(printExpression(saved().expression)).toBe('count(A) >= 1 && count(B) >= 1');
    fireEvent.click(screen.getByRole('button', { name: 'Advanced conditions' }));
    fireEvent.change(formula(), { target: { value: 'count(A) >=' } });
    expect(valid).toHaveBeenLastCalledWith(false);
    const accepted = saved().expression;
    fireEvent.change(screen.getByLabelText('Signal name A'), { target: { value: 'Reviewed' } });
    expect(formula().value).toBe('count(A) >=');
    expect(saved().expression).toEqual(accepted);
    fireEvent.change(formula(), { target: { value: '(count(A) + count(B)) * 2 >= 6' } });
    await waitFor(() => expect(valid).toHaveBeenLastCalledWith(true));
    expect(printExpression(saved().expression)).toBe('(count(A) + count(B)) * 2 >= 6');
  });

  it('applies a formula automatically after its missing signal is added', async () => {
    const valid = vi.fn();
    render(<Harness valid={valid} />);
    await screen.findByRole('option', { name: 'Agent Run \u00b7 Agent' });
    fireEvent.click(screen.getByRole('button', { name: 'Advanced conditions' }));
    fireEvent.change(formula(), { target: { value: 'count(B) >= 3' } });
    expect(valid).toHaveBeenLastCalledWith(false);
    fireEvent.click(screen.getByRole('button', { name: 'Add another event' }));
    await waitFor(() => expect(valid).toHaveBeenLastCalledWith(true));
    expect(formula().value).toBe('count(B) >= 3');
    expect(printExpression(saved().expression)).toBe('count(B) >= 3');
  });

  it('rejects oversized thresholds without throwing or changing the saved expression', async () => {
    const valid = vi.fn();
    render(<Harness valid={valid} />);
    await screen.findByRole('option', { name: 'Agent Run \u00b7 Agent' });
    fireEvent.change(screen.getByLabelText('Trigger behavior'), { target: { value: 'count' } });
    const accepted = saved().expression;
    fireEvent.change(screen.getByLabelText('Times'), { target: { value: '1000000000001' } });
    expect(valid).toHaveBeenLastCalledWith(false);
    expect(saved().expression).toEqual(accepted);
    fireEvent.change(screen.getByLabelText('Times'), { target: { value: '3' } });
    expect(valid).toHaveBeenLastCalledWith(true);
    expect(printExpression(saved().expression)).toBe('count(A) >= 3');
  });

  it('keeps a cleared time window invalid and accepts fractional seconds', async () => {
    const valid = vi.fn();
    render(<Harness valid={valid} start={{ ...initial(), window_seconds: 1.5 }} />);
    await screen.findByRole('option', { name: 'Agent Run \u00b7 Agent' });
    expect(valid).toHaveBeenLastCalledWith(true);
    fireEvent.click(screen.getByRole('button', { name: 'Advanced conditions' }));
    fireEvent.change(screen.getByLabelText('Window seconds'), { target: { value: '' } });
    expect(valid).toHaveBeenLastCalledWith(false);
    expect((screen.getByLabelText('Counting window') as HTMLSelectElement).value).toBe('limited');
    expect(saved().window_seconds).toBe(1.5);
    fireEvent.change(screen.getByLabelText('Window seconds'), { target: { value: '2.5' } });
    expect(valid).toHaveBeenLastCalledWith(true);
    expect(saved().window_seconds).toBe(2.5);
  });

  it('loads only live Agent grants, stores kind and target, and clears filters when changing source', async () => {
    render(<Harness />);
    const capability = await screen.findByRole('option', { name: 'Write a document · Report' });
    expect(worldApi.getStateMachineEvents).toHaveBeenCalledWith('live-agent');
    fireEvent.change(screen.getByLabelText('Trigger / interface'), { target: { value: (capability as HTMLOptionElement).value } });
    expect(saved().signals[0].match).toEqual({ entity_id: 'agent', event: 'capability.succeeded', capability: 'document.write', target_card_id: 'document' });
    fireEvent.click(screen.getByRole('button', { name: 'Advanced conditions' }));
    fireEvent.change(screen.getByLabelText('Trigger object'), { target: { value: 'group' } });
    expect(saved().signals[0].match).toEqual({ entity_id: 'group', event: 'unconfigured' });
    expect(screen.queryByRole('option', { name: 'Write a document \u00b7 Report' })).toBeNull();
  });
});
