import { describe, expect, it } from 'vitest';
import { createProgram, parseExpression } from './expressions';
import {
  connectStates, createEntity, readMachine, removeEntity, removeState,
  validateMachine, type MachineEntity, type StateMachine, type TransitionRule,
} from './model';

const entity = (id: string, parent_id?: string): MachineEntity => ({ ...createEntity(id, 'card', id, parent_id), id,
  initial_state: 'idle', states: ['idle', 'working', 'done', 'error'].map((state, index) => ({
    id: state, label: state === 'idle' ? 'Idle' : state, position: {x: index * 180, y: 90},
  })),
});
const rule = (id: string, extra: Partial<TransitionRule> = {}): TransitionRule => ({
  id, name: id, enabled: true, trigger: { entity_id: 'a', event: 'capability.completed' },
  conditions: [], effects: [{ entity_id: 'a', from_state: 'idle', to_state: 'working' }], ...extra,
});
const machine = (rules: TransitionRule[] = []): StateMachine => ({ version: 1, entities: [entity('a'), entity('b')], rules });

it('preserves system contracts and uses state anchors or registered commands without system effects', () => {
  const system: MachineEntity = {...entity('execution'), ownership: 'system', owner: 'host.run_manager',
    projection: [{event: 'agent.work_started', from_state: '*', to_state: 'working'}],
    commands: [{id: 'start_work', label: 'Start work', kind: 'run', operation_id: 'host:run', input_schema: {}, authorization: [], outcomes: ['working']}]};
  const value: StateMachine = {version: 2, entities: [system, entity('workflow')], rules: []};
  expect(readMachine(value)).toEqual(value);
  expect(removeState(value, system.id, 'working')).toBe(value);
  expect(removeEntity(value, system.id)).toBe(value);
  const reaction = connectStates(value, {entityId: 'execution', stateId: 'working'}, {entityId: 'workflow', stateId: 'working'});
  expect(reaction.trigger).toEqual({entity_id: 'execution', event: 'state.entered', state_id: 'working'});
  expect(reaction.enabled).toBe(true);
  const request = connectStates(value, {entityId: 'workflow', stateId: 'idle'}, {entityId: 'execution', stateId: 'working'});
  expect(request.effects).toEqual([]);
  expect(request.command).toMatchObject({entity_id: 'execution', state_id: 'working', command_id: 'start_work'});
  expect(validateMachine({...value, rules: [reaction, request]})).toEqual([]);
  expect(() => connectStates(value, {entityId: 'workflow', stateId: 'idle'}, {entityId: 'execution', stateId: 'error'})).toThrow('no registered command');
  expect(validateMachine({...value, rules: [{...reaction, effects: [{entity_id: 'execution', from_state: '*', to_state: 'working'}]}]})).toContain('System states cannot be assigned. Choose a legal command.');
});

describe('state machine configuration', () => {
  it('creates an empty draft group without inventing business states', () => {
    const group = createEntity('Research', 'group');
    expect(group.states).toEqual([]);
    expect(group.initial_state).toBe('');
    expect(validateMachine({version: 2, entities: [group], rules: []})).not.toEqual([]);
  });

  it('round trips valid JSON with wildcard transitions and detaches imported data', () => {
    const original = machine([rule('all', { effects: [{ entity_id: 'a', from_state: '*', to_state: 'done' }] })]);
    const restored = readMachine(JSON.parse(JSON.stringify(original)));
    expect(restored).toEqual(original);
    restored!.entities[0].states[0].label = 'Changed';
    expect(original.entities[0].states[0].label).toBe('Idle');
  });

  it('normalizes optional null fields and names the same way as persisted backend config', () => {
    expect(createEntity('Root', 'group', null, null)).not.toHaveProperty('parent_id');
    expect(createEntity('Root')).not.toHaveProperty('card_id');
    const value = machine([rule('transition')]);
    value.entities[0].parent_id = null;
    value.entities[0].label = ' Agent A ';
    value.entities[0].states[0].label = ' Idle ';
    value.rules[0].name = ' Finish ';
    const result = readMachine(value)!;
    expect(result.entities[0]).not.toHaveProperty('parent_id');
    expect(result.rules[0].trigger).not.toHaveProperty('capability');
    expect(result.entities[0].label).toBe('Agent A');
    expect(result.entities[0].states[0].label).toBe('Idle');
    expect(result.rules[0].name).toBe('Finish');
  });

  it('enforces backend identifier, label, coordinate and collection limits before save', () => {
    const tooManyStates = machine();
    tooManyStates.entities[0].states = Array.from({ length: 101 }, (_, index) => ({ id: `s${index}`, label: 'State', position: { x: 0, y: 0 } }));
    tooManyStates.entities[0].initial_state = 's0';
    expect(validateMachine(tooManyStates).some(error => error.includes('100 states'))).toBe(true);
    const tooManyEntities = machine();
    tooManyEntities.entities = Array.from({ length: 201 }, (_, index) => entity(`e${index}`));
    expect(validateMachine(tooManyEntities).some(error => error.includes('200 entities'))).toBe(true);
    const tooManyRules = machine(Array.from({ length: 1001 }, (_, index) => rule(`r${index}`)));
    expect(validateMachine(tooManyRules).some(error => error.includes('1000 rules'))).toBe(true);
    const fields = machine([rule('rule', { trigger: { entity_id: 'a', event: 'event with spaces', capability: 'x'.repeat(129) } })]);
    fields.entities[0].label = 'x'.repeat(201);
    fields.entities[0].states[0].position.x = 1_000_001;
    expect(validateMachine(fields)).toHaveLength(4);
  });

  it('rejects malformed structures, unsupported versions, bad coordinates and broken references', () => {
    const badPosition = machine();
    badPosition.entities[0].states[0].position.x = Infinity;
    const badRule = machine([rule('bad', { effects: [{ entity_id: 'missing', from_state: '*', to_state: 'done' }] })]);
    const badInitial = machine();
    badInitial.entities[0].initial_state = 'unknown';
    for (const candidate of [null, [], {}, { ...machine(), version: 3 }, { ...machine(), entities: [null] },
      { ...machine(), rules: [{ ...rule('bad'), trigger: null }] }, badPosition, badRule, badInitial]) {
      expect(readMachine(candidate)).toBeNull();
    }
  });

  it('validates ownership cycles, duplicate IDs and conflicting writes', () => {
    const circular = machine();
    circular.entities[0].parent_id = 'b';
    circular.entities[1].parent_id = 'a';
    expect(validateMachine(circular).some(error => error.includes('circular'))).toBe(true);
    const duplicate = machine();
    duplicate.entities.push(duplicate.entities[0]);
    expect(validateMachine(duplicate)).toContain('Entity IDs must be unique.');
    const conflict = machine([rule('conflict', { effects: [
      { entity_id: 'a', from_state: 'idle', to_state: 'done' },
      { entity_id: 'a', from_state: 'idle', to_state: 'error' },
    ] })]);
    expect(validateMachine(conflict).some(error => error.includes('change each entity only once'))).toBe(true);
  });
});

describe('configuration deletion', () => {
  it('removes state references and repairs the initial state without mutating the source', () => {
    const original = machine([rule('deleted'), rule('guard', {
      conditions: [{ entity_id: 'a', state_id: 'idle' }], effects: [{ entity_id: 'b', from_state: '*', to_state: 'done' }],
    }), rule('retained', { effects: [{ entity_id: 'b', from_state: 'idle', to_state: 'done' }] })]);
    const result = removeState(original, 'a', 'idle');
    expect(result.entities[0].initial_state).toBe('working');
    expect(result.rules.map(item => item.id)).toEqual(['retained']);
    expect(validateMachine(result)).toEqual([]);
    expect(original.entities[0].states).toHaveLength(4);
    expect(original.rules).toHaveLength(3);
  });

  it('keeps at least one state and ignores nonexistent state IDs', () => {
    const config = machine();
    config.entities[0].states = config.entities[0].states.slice(0, 1);
    expect(removeState(config, 'a', 'idle')).toBe(config);
    expect(removeState(config, 'b', 'missing')).toBe(config);
  });

  it('deletes a group’s entire subtree and all event, condition and effect references', () => {
    const config: StateMachine = {
      version: 1, entities: [entity('root'), entity('a', 'root'), entity('b', 'a'), entity('c', 'b'), entity('keep', 'root')],
      rules: [rule('source'), rule('condition', { trigger: { entity_id: 'root', event: 'event' },
        conditions: [{ entity_id: 'b', state_id: 'idle' }], effects: [{ entity_id: 'keep', from_state: '*', to_state: 'done' }] }),
      rule('effect', { trigger: { entity_id: 'root', event: 'event' }, effects: [{ entity_id: 'c', from_state: '*', to_state: 'done' }] }),
      rule('keep', { trigger: { entity_id: 'root', event: 'event' }, effects: [{ entity_id: 'keep', from_state: '*', to_state: 'done' }] })],
    };
    const result = removeEntity(config, 'a');
    expect(result.entities.map(item => item.id)).toEqual(['root', 'keep']);
    expect(result.rules.map(item => item.id)).toEqual(['keep']);
    expect(validateMachine(result)).toEqual([]);
    expect(config.entities).toHaveLength(5);
  });
});

describe('version 2 trigger program integration', () => {
  it('creates version 2 configurations while preserving saved version 1 unchanged', () => {
    const saved = machine([rule('legacy')]);
    expect(readMachine(saved)).toEqual(saved);
    expect(readMachine(saved)?.version).toBe(1);
    expect(readMachine({ ...saved, version: 2 })?.version).toBe(2);
  });

  it('only accepts programs in version 2 and validates every program reference', () => {
    const programmed = rule('programmed');
    programmed.program = createProgram(programmed.trigger);
    programmed.program.expression = parseExpression('count(A) >= 3 && state("b", "idle")');
    const config = { ...machine([programmed]), version: 2 as const };
    expect(readMachine(config)).toEqual(config);
    expect(readMachine({ ...config, version: 1 })).toBeNull();
    expect(validateMachine({ ...config, version: 1 })).toContain('Trigger programs require state machine version 2.');
    programmed.program.signals[0].match.entity_id = 'missing';
    expect(readMachine(config)).toBeNull();
    expect(validateMachine(config).some(error => error.includes('signal "A"'))).toBe(true);
  });

  it('cascades state deletion through expression state checks even when conditions and effects do not reference that state', () => {
    const programmed = rule('programmed', { trigger: { entity_id: 'b', event: 'run.completed' },
      effects: [{ entity_id: 'b', from_state: 'idle', to_state: 'done' }] });
    programmed.program = createProgram(programmed.trigger);
    programmed.program.expression = parseExpression('event(A) && state("a", "idle")');
    const config = { ...machine([programmed]), version: 2 as const };
    const result = removeState(config, 'a', 'idle');
    expect(result.rules).toEqual([]);
    expect(result.entities[0].initial_state).toBe('working');
    expect(config.rules).toHaveLength(1);
  });

  it('cascades entity deletion through signal sources and deeply nested state checks', () => {
    const base = rule('by-signal', { trigger: { entity_id: 'b', event: 'run.completed' },
      effects: [{ entity_id: 'b', from_state: 'idle', to_state: 'done' }] });
    const bySignal = { ...base, program: createProgram({ entity_id: 'a', event: 'run.completed' }) };
    const byState = { ...base, id: 'by-state', program: createProgram(base.trigger) };
    byState.program.expression = parseExpression('event(A) && !state("a", "error")');
    const retained = { ...base, id: 'retained', program: createProgram(base.trigger) };
    const result = removeEntity({ ...machine([bySignal, byState, retained]), version: 2 }, 'a');
    expect(result.rules.map(item => item.id)).toEqual(['retained']);
    expect(validateMachine(result)).toEqual([]);
  });

});
