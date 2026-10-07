import { describe, expect, it } from 'vitest';
import { machineFromDocument, projectView, ruleForOwner, splitMachine, type MachineEntry } from './documents';
import { validateMachine, type MachineEntity, type StateMachine, type TransitionRule } from './model';

const entity = (id: string, parent_id?: string): MachineEntity => ({
  id, label: id, kind: 'group', ...(parent_id ? { parent_id } : {}), initial_state: 'ready',
  states: [{ id: 'ready', label: 'Ready', position: { x: 10, y: 20 } }, { id: 'done', label: 'Done', position: { x: 40, y: 50 } }],
});
const rule = (): TransitionRule => ({
  id: 'transition', name: 'Proceed', enabled: true, trigger: { entity_id: 'workflow', event: 'run.completed' },
  conditions: [{ entity_id: 'nested', state_id: 'ready' }],
  effects: [{ entity_id: 'workflow', from_state: 'ready', to_state: 'done' }],
  program: { reset: 'on_match', signals: [{ id: 'A', label: 'Completion', match: { entity_id: 'workflow', event: 'run.completed' } }],
    expression: { op: 'all', args: [{ op: 'event', signal: 'A' }, { op: 'not', arg: { op: 'state', entity_id: 'nested', state_id: 'done' } }] } },
  actions: [{ id: 'action', kind: 'run', caller: { kind: 'current' }, target: { kind: 'specific', card_id: 'target' },
    arguments: { entity_id: 'workflow', prompt: 'An opaque plugin argument' } }],
});
const entry = (cardId: string, version = 3): MachineEntry => ({ cardId, version,
  machine: { version: 2, entities: [entity('workflow'), entity('nested', 'workflow')], rules: [rule()] } });

describe('authoritative document projection', () => {
  it('separates same-template member entities and rules without changing owner definitions', () => {
    const root = entry('root'), first = entry('member-a'), second = entry('member-b');
    const before = structuredClone([root, first, second]);
    const view = projectView('root', [root, first, second]);
    expect(new Set(view.machine.entities.map(entity => entity.id)).size).toBe(6);
    expect(new Set(view.machine.rules.map(rule => rule.id)).size).toBe(3);
    expect(view.entityIds.root.workflow).toBe('workflow');
    expect(view.entityIds['member-a'].workflow).not.toBe(view.entityIds['member-b'].workflow);
    for (const member of [first, second]) {
      const groupId = view.entityIds[member.cardId].workflow;
      const childId = view.entityIds[member.cardId].nested;
      const projected = view.machine.rules.find(rule => view.ruleOwners[rule.id].cardId === member.cardId)!;
      expect(view.entityOwners[groupId]).toEqual({ cardId: member.cardId, entityId: 'workflow' });
      expect(view.machine.entities.find(entity => entity.id === childId)?.parent_id).toBe(groupId);
      expect(projected.trigger.entity_id).toBe(groupId);
      expect(projected.conditions[0].entity_id).toBe(childId);
      expect(projected.effects[0].entity_id).toBe(groupId);
      expect(projected.program?.signals[0].match.entity_id).toBe(groupId);
      expect(projected.program?.expression).toEqual({ op: 'all', args: [{ op: 'event', signal: 'A' },
        { op: 'not', arg: { op: 'state', entity_id: childId, state_id: 'done' } }] });
      expect(projected.actions).toEqual(member.machine.rules[0].actions);
      expect(view.references.find(reference => reference.entity_id === groupId)).toMatchObject({ card_id: member.cardId, state_group_id: 'workflow', definition_version: 3 });
    }
    expect(validateMachine(view.machine)).toEqual([]);
    expect([root, first, second]).toEqual(before);
    expect(view.machine.references).toBeUndefined();
    expect(projectView('root', [second, first, root])).toEqual(view);
  });

  it('reuses saved member aliases and translates edits back to the real owner', () => {
    const root = entry('root'), member = entry('member-a', 7);
    root.machine.references = [{ entity_id: 'lead', card_id: member.cardId, state_group_id: 'workflow', definition_version: 7 }];
    root.machine.rules[0].effects.push({ entity_id: 'lead', from_state: '*', to_state: 'done' });
    const view = projectView('root', [root, member]);
    expect(view.entityIds[member.cardId].workflow).toBe('lead');
    const selected = view.machine.rules.find(rule => view.ruleOwners[rule.id].cardId === member.cardId)!;
    const updated = ruleForOwner(view, member, { ...selected, name: 'Edited member',
      effects: [...selected.effects, { entity_id: 'workflow', from_state: '*', to_state: 'done' }] });
    expect(updated.rule.id).toBe('transition');
    expect(updated.rule.trigger.entity_id).toBe('workflow');
    expect(updated.rule.effects[0].entity_id).toBe('workflow');
    expect(updated.rule.effects[1].entity_id).not.toBe('workflow');
    expect(updated.references).toContainEqual({ entity_id: updated.rule.effects[1].entity_id,
      card_id: 'root', state_group_id: 'workflow', definition_version: 3 });
    expect(root.machine.rules[0].name).toBe('Proceed');
    const saved: StateMachine = { ...member.machine, rules: [updated.rule], references: updated.references };
    expect(validateMachine(saved)).toEqual([]);
  });

  it('maps member cross-object references to a loaded group and retains unloaded references', () => {
    const root = entry('root'), member = entry('member');
    member.machine.references = [{ entity_id: 'coordinator', card_id: 'root', state_group_id: 'workflow' },
      { entity_id: 'not-loaded', card_id: 'deep-member', state_group_id: 'workflow', definition_version: 2 }];
    member.machine.rules[0].effects.push({ entity_id: 'coordinator', from_state: '*', to_state: 'done' });
    const view = projectView('root', [root, member]);
    const displayed = view.machine.rules.find(rule => view.ruleOwners[rule.id].cardId === member.cardId)!;
    expect(displayed.effects[1].entity_id).toBe('workflow');
    expect(view.machine.references).toEqual([expect.objectContaining({ card_id: 'deep-member', state_group_id: 'workflow' })]);
    const edited = ruleForOwner(view, member, displayed);
    expect(edited.rule).toEqual(member.machine.rules[0]);
    expect(edited.references).toEqual(member.machine.references);
    expect(view.machine.entities).toHaveLength(4);
  });

  it('bounds aliases and avoids collisions with arbitrary existing IDs', () => {
    const root = entry('root'), member = entry('member');
    root.machine.entities.push(entity('ref:member:workflow'));
    member.machine.entities.push(entity('g'.repeat(128)));
    const view = projectView('root', [root, member]);
    expect(view.entityIds.member.workflow).not.toBe('ref:member:workflow');
    expect(view.machine.entities.every(entity => entity.id.length <= 128)).toBe(true);
    expect(new Set(view.machine.entities.map(entity => entity.id)).size).toBe(view.machine.entities.length);
    expect(validateMachine(view.machine)).toEqual([]);
  });

  it('keeps presentation separate when storing and reopening an authoritative document', () => {
    const original = entry('root').machine;
    const split = splitMachine(original);
    expect(split.definition.entities[0].states[0]).not.toHaveProperty('position');
    expect(split.presentation.positions?.workflow.ready).toEqual({ x: 10, y: 20 });
    expect(machineFromDocument({ ...split, definition_version: 1, revision: 1, enabled: false })).toEqual(original);
  });

  it('merges legacy group coordinates without collisions and preserves moves on reopening', () => {
    const split = splitMachine(entry('root').machine);
    delete split.presentation.coordinate_space;
    const doc = {...split, definition_version: 1, revision: 1, enabled: false};
    const migrated = machineFromDocument(doc)!;
    const [first, second] = migrated.entities;
    expect(first.states).toEqual(entry('root').machine.entities[0].states);
    for (const state of second.states) for (const other of first.states) {
      expect(Math.abs(state.position.x - other.position.x) >= 150 || Math.abs(state.position.y - other.position.y) >= 170).toBe(true);
    }
    expect(second.states[0].position).not.toEqual(second.states[1].position);
    migrated.entities[1].states[0].position = {x: 350, y: 420};
    const saved = splitMachine(migrated);
    expect(saved.presentation.coordinate_space).toBe('owner');
    expect(machineFromDocument({...doc, ...saved})).toEqual(migrated);
    expect(doc.presentation.positions).toEqual(split.presentation.positions);
  });

  it('reserves a relocated legacy state when placing later states from its own group', () => {
    const original = entry('root').machine;
    original.rules = [];
    original.entities[0].states = [{id: 'ready', label: 'Ready', position: {x: 65, y: 110}}];
    original.entities[1].states = [
      {id: 'ready', label: 'Ready', position: {x: 65, y: 110}},
      {id: 'done', label: 'Done', position: {x: 285, y: 110}},
    ];
    const split = splitMachine(original);
    delete split.presentation.coordinate_space;
    const states = machineFromDocument({...split, definition_version: 1, revision: 1, enabled: false})!.entities.flatMap(entity => entity.states);
    expect(new Set(states.map(state => JSON.stringify(state.position))).size).toBe(states.length);
  });
});
