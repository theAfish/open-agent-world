import { expect, it } from 'vitest';
import { connectionOffsets, stateConnections } from './graphRepresentation';
import { machineSpaces, nextStatePosition } from './graphLayout';
import type { MachineEntity, StateMachine, TransitionRule } from './model';

const group = (id: string, ownership: 'system' | 'user', states: string[]): MachineEntity => ({id, label: id, kind: 'group', ownership,
  initial_state: states[0], states: states.map((id, index) => ({id, label: id, position: {x: 65, y: 110 + index * 150}}))});
const execution = group('execution', 'system', ['idle', 'running', 'waiting', 'error']);
const workflow = group('workflow', 'user', ['researching', 'reviewing']);
const rule = (patch: Partial<TransitionRule>): TransitionRule => ({id: 'rule', name: 'Rule', enabled: true, trigger: {entity_id: 'execution', event: 'agent.work_started'}, conditions: [], effects: [], ...patch});

it('shares one owner space across system and user groups, including nested groups', () => {
  const system = {...execution, card_id: 'agent'};
  const user = {...workflow, card_id: 'agent', parent_id: 'execution'};
  const member = {...workflow, id: 'member-workflow', card_id: 'member'};
  const spaces = machineSpaces({version: 2, entities: [system, user, member], rules: []});
  expect(spaces.map(space => [space.id, space.entities.map(entity => entity.id)])).toEqual([
    ['agent', ['execution', 'workflow']], ['member', ['member-workflow']],
  ]);
  const position = nextStatePosition([...system.states, ...user.states]);
  for (const state of [...system.states, ...user.states]) {
    expect(Math.abs(position.x - state.position.x) >= 150 || Math.abs(position.y - state.position.y) >= 170).toBe(true);
  }
});

it('assigns stable distinct lanes to parallel, return, and self-loop connections', () => {
  const edges = stateConnections({version: 2, entities: [workflow], rules: [
    rule({id: 'out', effects: [{entity_id: 'workflow', from_state: 'researching', to_state: 'reviewing'}]}),
    rule({id: 'back', effects: [{entity_id: 'workflow', from_state: 'reviewing', to_state: 'researching'}]}),
    rule({id: 'loop-1', effects: [{entity_id: 'workflow', from_state: 'researching', to_state: 'researching'}]}),
    rule({id: 'loop-2', effects: [{entity_id: 'workflow', from_state: 'researching', to_state: 'researching'}]}),
  ]});
  const offsets = connectionOffsets(edges);
  expect(offsets.get(edges[0].id)).not.toBe(0);
  // Reverse edges have the same local sign: their normals point opposite ways.
  expect(offsets.get(edges[0].id)).toBe(offsets.get(edges[1].id));
  expect(offsets.get(edges[2].id)).not.toBe(offsets.get(edges[3].id));
  expect(connectionOffsets([...edges].reverse())).toEqual(offsets);
});

it('represents canonical wildcard facts with dimmable state-to-state connections, never synthetic endpoints', () => {
  const machine: StateMachine = {version: 2, entities: [execution], rules: [rule({canonical: true, effects: [{entity_id: 'execution', from_state: '*', to_state: 'running'}]})]};
  const before = structuredClone(machine);
  const edges = stateConnections(machine);
  expect(edges.map(edge => edge.source.stateId)).toEqual(['idle', 'waiting', 'error']);
  expect(edges.every(edge => edge.target.stateId === 'running' && edge.rule.canonical)).toBe(true);
  expect(machine).toEqual(before);
});

it('deduplicates visual canonical edges sharing endpoints without changing runtime projections', () => {
  const finish = rule({id: 'finished', canonical: true, effects: [{entity_id: 'execution', from_state: '*', to_state: 'idle'}]});
  const ready = {...finish, id: 'ready', trigger: {...finish.trigger, event: 'agent.ready'}};
  expect(stateConnections({version: 2, entities: [execution], rules: [finish, ready]})).toHaveLength(3);
});

it('keeps reactions and operation requests as distinct edges between real states', () => {
  const reaction = rule({trigger: {entity_id: 'execution', state_id: 'running', event: 'state.entered'}, effects: [{entity_id: 'workflow', from_state: '*', to_state: 'researching'}]});
  const request = rule({id: 'request', trigger: {entity_id: 'workflow', state_id: 'reviewing', event: 'state.entered'}, command: {entity_id: 'execution', state_id: 'running', command_id: 'start_work', arguments: {prompt: 'Research'}}});
  const edges = stateConnections({version: 2, entities: [execution, workflow], rules: [reaction, request]});
  expect(edges.map(edge => [edge.source.stateId, edge.target.stateId, edge.command])).toEqual([['running', 'researching', false], ['reviewing', 'running', true]]);
});

it('renders editable wildcard user rules and hides transitions to absent reference states', () => {
  const any = rule({trigger: {entity_id: 'workflow', event: 'custom'}, effects: [{entity_id: 'workflow', from_state: '*', to_state: 'reviewing'}]});
  const hidden = rule({id: 'hidden', trigger: {entity_id: 'execution', state_id: 'idle', event: 'state.entered'}, effects: any.effects});
  const edges = stateConnections({version: 2, entities: [workflow], rules: [any, hidden]});
  expect(edges.map(edge => edge.source.stateId)).toEqual(['researching', 'reviewing']);
  expect(edges.every(edge => edge.rule.id === 'rule')).toBe(true);
});
