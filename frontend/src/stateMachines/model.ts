import { expressionReferences, readProgram, validateProgram, type TriggerProgram } from './expressions';
import type { PluginCatalog, WorldCard } from '../types/world';
export type { TriggerExpression, TriggerProgram, TriggerSignal } from './expressions';
export { createProgram, programSignals } from './expressions';

export interface MachineState {
  id: string;
  label: string;
  position: { x: number; y: number };
}

export interface MachineEntity {
  id: string;
  label: string;
  kind: 'card' | 'group' | 'spawn';
  ownership?: 'system' | 'user';
  owner?: string;
  projection?: { event: string; label?: string; from_state: string; to_state: string; operation_id?: string }[];
  commands?: SystemCommand[];
  card_id?: string | null;
  parent_id?: string | null;
  initial_state: string;
  states: MachineState[];
}

export interface SystemCommand {
  id: string;
  label: string;
  kind: MachineAction['kind'];
  operation_id: string;
  capability?: string;
  action?: string;
  input_schema: Record<string, unknown>;
  authorization: string[];
  outcomes: string[];
}

export interface MachineEvent {
  entity_id: string;
  event: string;
  capability?: string;
  target_card_id?: string;
  operation_id?: string;
  state_id?: string;
}

export interface MachineAction {
  id: string;
  kind: 'capability' | 'node_action' | 'run';
  operation_id?: string;
  capability?: string;
  action?: string;
  target: { kind: 'current' | 'specific' | 'associated' | 'produced'; card_id?: string; index?: number };
  caller: MachineAction['target'];
  arguments: Record<string, unknown>;
}

export interface TransitionRule {
  id: string;
  name: string;
  enabled: boolean;
  trigger: MachineEvent;
  conditions: { entity_id: string; state_id: string }[];
  effects: { entity_id: string; from_state: string; to_state: string }[];
  program?: TriggerProgram;
  actions?: MachineAction[];
  command?: { entity_id: string; state_id: string; command_id: string; arguments: Record<string, unknown> };
  /** View-only canonical edge; never saved as a user rule. */
  canonical?: boolean;
}

export interface StateMachine {
  version: 1 | 2;
  status_entity_id?: string;
  entities: MachineEntity[];
  rules: TransitionRule[];
  references?: { entity_id: string; card_id: string; definition_version?: number; state_group_id?: string }[];
}

export function makeId(prefix: string): string {
  return `${prefix}_${globalThis.crypto.randomUUID()}`;
}

export function stateMachineEditorAvailable(card: WorldCard, catalog: PluginCatalog): boolean {
  return !card.ephemeral && !card.missing_plugin
    && catalog.node_types.some(type => type.id === card.type && type.state_machine_editor === true);
}

export function createEntity(
  label: string,
  kind: MachineEntity['kind'] = 'card',
  card_id?: string | null,
  parent_id?: string | null,
): MachineEntity {
  return {
    id: makeId('entity'), label, kind, ownership: 'user',
    ...(card_id == null ? {} : { card_id }), ...(parent_id == null ? {} : { parent_id }),
    initial_state: '',
    states: [],
  };
}

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const isText = (value: unknown): value is string => typeof value === 'string';
const optionalText = (value: unknown): value is string | undefined => value === undefined || isText(value);
const nullableText = (value: unknown): value is string | null | undefined => value === null || optionalText(value);
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

/** Reject malformed, unsupported or inconsistent persisted/imported data. */
export function readMachine(value: unknown): StateMachine | null {
  if (!isObject(value) || (value.version !== 1 && value.version !== 2) || !Array.isArray(value.entities) || !Array.isArray(value.rules)) return null;
  const entities: MachineEntity[] = [];
  for (const entity of value.entities) {
    if (!isObject(entity) || !isText(entity.id) || !isText(entity.label)
      || !['card', 'group', 'spawn'].includes(String(entity.kind))
      || !nullableText(entity.card_id) || !nullableText(entity.parent_id)
      || !isText(entity.initial_state) || !Array.isArray(entity.states)) return null;
    const states: MachineState[] = [];
    for (const state of entity.states) {
      if (!isObject(state) || !isText(state.id) || !isText(state.label) || !isObject(state.position)
        || !finite(state.position.x) || !finite(state.position.y)) return null;
      states.push({ id: state.id, label: state.label.trim(), position: { x: state.position.x, y: state.position.y } });
    }
    entities.push({ id: entity.id, label: entity.label.trim(), kind: entity.kind as MachineEntity['kind'],
      ...(entity.ownership === 'system' || entity.ownership === 'user' ? { ownership: entity.ownership } : {}),
      ...(typeof entity.owner === 'string' ? { owner: entity.owner } : {}),
      ...(Array.isArray(entity.projection) ? { projection: structuredClone(entity.projection) as MachineEntity['projection'] } : {}),
      ...(Array.isArray(entity.commands) ? { commands: structuredClone(entity.commands) as SystemCommand[] } : {}),
      ...(entity.card_id == null ? {} : { card_id: entity.card_id }),
      ...(entity.parent_id == null ? {} : { parent_id: entity.parent_id }), initial_state: entity.initial_state, states });
  }
  const rules: TransitionRule[] = [];
  for (const rule of value.rules) {
    if (!isObject(rule) || !isText(rule.id) || !isText(rule.name) || typeof rule.enabled !== 'boolean'
      || !isObject(rule.trigger) || !isText(rule.trigger.entity_id) || !isText(rule.trigger.event)
      || !nullableText(rule.trigger.capability) || !nullableText(rule.trigger.target_card_id) || !nullableText(rule.trigger.operation_id) || !nullableText(rule.trigger.state_id)
      || !Array.isArray(rule.conditions) || !Array.isArray(rule.effects)) return null;
    const conditions: TransitionRule['conditions'] = [];
    for (const condition of rule.conditions) {
      if (!isObject(condition) || !isText(condition.entity_id) || !isText(condition.state_id)) return null;
      conditions.push({ entity_id: condition.entity_id, state_id: condition.state_id });
    }
    const effects: TransitionRule['effects'] = [];
    for (const effect of rule.effects) {
      if (!isObject(effect) || !isText(effect.entity_id) || !isText(effect.from_state) || !isText(effect.to_state)) return null;
      effects.push({ entity_id: effect.entity_id, from_state: effect.from_state, to_state: effect.to_state });
    }
    const program = rule.program == null ? undefined : readProgram(rule.program);
    if (program === null) return null;
    rules.push({ id: rule.id, name: rule.name.trim(), enabled: rule.enabled,
      trigger: { entity_id: rule.trigger.entity_id, event: rule.trigger.event,
        ...(rule.trigger.capability == null ? {} : { capability: rule.trigger.capability }),
        ...(rule.trigger.target_card_id == null ? {} : { target_card_id: rule.trigger.target_card_id }),
        ...(rule.trigger.operation_id == null ? {} : { operation_id: rule.trigger.operation_id }),
        ...(rule.trigger.state_id == null ? {} : { state_id: rule.trigger.state_id }) }, conditions, effects,
      ...(program ? { program } : {}),
      ...(isObject(rule.command) ? { command: structuredClone(rule.command) as TransitionRule['command'] } : {}),
      ...(Array.isArray(rule.actions) ? { actions: structuredClone(rule.actions) as MachineAction[] } : {}) });
  }
  const references: NonNullable<StateMachine['references']> = [];
  if (value.references != null) {
    if (!Array.isArray(value.references)) return null;
    for (const reference of value.references) {
      if (!isObject(reference) || !isText(reference.entity_id) || !isText(reference.card_id)) return null;
      references.push({ entity_id: reference.entity_id, card_id: reference.card_id,
        ...(typeof reference.definition_version === 'number' ? { definition_version: reference.definition_version } : {}),
        ...(typeof reference.state_group_id === 'string' ? { state_group_id: reference.state_group_id } : {}) });
    }
  }
  if (value.status_entity_id != null && (!isText(value.status_entity_id) || !entities.some(entity => entity.id === value.status_entity_id))) return null;
  const machine: StateMachine = { version: value.version, entities, rules,
    ...(typeof value.status_entity_id === 'string' ? { status_entity_id: value.status_entity_id } : {}),
    ...(references.length ? { references } : {}) };
  return validateMachine(machine).length ? null : machine;
}

export function validateMachine(machine: StateMachine): string[] {
  const errors: string[] = [];
  const identifier = (value: string) => value.length >= 1 && value.length <= 128 && /^[^\s\x00]+$/.test(value);
  const label = (value: string) => value.trim().length >= 1 && value.trim().length <= 200;
  const entities = new Map(machine.entities.map(entity => [entity.id, entity]));
  const references = new Set(machine.references?.map(reference => reference.entity_id));
  const hasState = (entityId: string, stateId: string) => references.has(entityId) || entities.get(entityId)?.states.some(state => state.id === stateId);
  if (machine.version !== 1 && machine.version !== 2) errors.push('Only state machine versions 1 and 2 are supported.');
  if (!machine.entities.length) errors.push('Add at least one entity.');
  if (machine.entities.length > 200) errors.push('A state machine can contain at most 200 entities.');
  if (machine.rules.length > 1000) errors.push('A state machine can contain at most 1000 rules.');
  if (machine.entities.reduce((total, entity) => total + entity.states.length, 0) > 5000) errors.push('A state machine can contain at most 5000 states.');
  if (entities.size !== machine.entities.length) errors.push('Entity IDs must be unique.');
  for (const entity of machine.entities) {
    if (!identifier(entity.id)) errors.push('Entity IDs must be 1–128 characters without whitespace.');
    if (!label(entity.label)) errors.push('Entity names must be 1–200 characters.');
    if (entity.card_id != null && !identifier(entity.card_id)) errors.push('Card IDs must be 1–128 characters without whitespace.');
    if (entity.parent_id != null && !identifier(entity.parent_id)) errors.push('Parent IDs must be 1–128 characters without whitespace.');
    if (!entity.states.length) errors.push(`Entity "${entity.label}" needs at least one state.`);
    if (entity.states.length > 100) errors.push(`Entity "${entity.label}" can contain at most 100 states.`);
    const ids = new Set(entity.states.map(state => state.id));
    if (ids.size !== entity.states.length) errors.push(`State IDs in "${entity.label}" must be unique.`);
    if (!ids.has(entity.initial_state)) errors.push(`Choose an initial state for "${entity.label}".`);
    for (const state of entity.states) {
      if (!identifier(state.id) || state.id === '*') errors.push('State IDs must be 1–128 characters without whitespace and cannot be "*".');
      if (!label(state.label)) errors.push('State names must be 1–200 characters.');
      if (!finite(state.position.x) || !finite(state.position.y) || Math.abs(state.position.x) > 1_000_000 || Math.abs(state.position.y) > 1_000_000) errors.push(`State positions in "${entity.label}" must be between -1000000 and 1000000.`);
    }
    if (entity.parent_id && !entities.has(entity.parent_id)) errors.push(`Parent of "${entity.label}" does not exist.`);
    const visited = new Set([entity.id]);
    let parent = entity.parent_id;
    while (parent && entities.has(parent)) {
      if (visited.has(parent)) { errors.push(`Entity "${entity.label}" belongs to a circular group.`); break; }
      visited.add(parent);
      parent = entities.get(parent)!.parent_id;
    }
  }
  if (new Set(machine.rules.map(rule => rule.id)).size !== machine.rules.length) errors.push('Rule IDs must be unique.');
  for (const rule of machine.rules) {
    if (rule.program) {
      if (machine.version === 1) errors.push('Trigger programs require state machine version 2.');
      errors.push(...validateProgram(rule.program, references.size ? undefined : machine).map(error => `Rule "${rule.name}": ${error}`));
    }
    if (!identifier(rule.id)) errors.push('Rule IDs must be 1–128 characters without whitespace.');
    if (!label(rule.name)) errors.push('Rule names must be 1–200 characters.');
    if (!entities.has(rule.trigger.entity_id) && !references.has(rule.trigger.entity_id)) errors.push(`Event source for "${rule.name}" does not exist.`);
    if (!identifier(rule.trigger.event)) errors.push('Events must be 1–128 characters without whitespace.');
    if (rule.trigger.capability != null && !identifier(rule.trigger.capability)) errors.push('Capabilities must be 1–128 characters without whitespace.');
    if (rule.trigger.target_card_id != null && !identifier(rule.trigger.target_card_id)) errors.push('Target card IDs must be 1–128 characters without whitespace.');
    if (!rule.effects.length && !rule.command) errors.push(`Rule "${rule.name}" needs a transition or system command.`);
    if (rule.command && entities.has(rule.command.entity_id)) {
      const group = entities.get(rule.command.entity_id)!;
      if (group.ownership !== 'system' || !group.commands?.some(command => command.id === rule.command!.command_id && command.outcomes.includes(rule.command!.state_id))) errors.push('Choose a legal command for this system state.');
    }
    if (rule.effects.length > 200 || rule.conditions.length > 200) errors.push(`Rule "${rule.name}" can contain at most 200 conditions and 200 transitions.`);
    for (const condition of rule.conditions) {
      if (!hasState(condition.entity_id, condition.state_id)) errors.push(`Condition in "${rule.name}" refers to a missing state.`);
    }
    const conditionEntities = new Set(rule.conditions.map(condition => condition.entity_id));
    if (conditionEntities.size !== rule.conditions.length) errors.push(`Rule "${rule.name}" can check each entity only once.`);
    const changed = new Set<string>();
    for (const effect of rule.effects) {
      if (entities.get(effect.entity_id)?.ownership === 'system') errors.push('System states cannot be assigned. Choose a legal command.');
      if (!hasState(effect.entity_id, effect.to_state)
        || (effect.from_state !== '*' && !hasState(effect.entity_id, effect.from_state))) errors.push(`Transition in "${rule.name}" refers to a missing state.`);
      if (changed.has(effect.entity_id)) errors.push(`Rule "${rule.name}" can change each entity only once.`);
      changed.add(effect.entity_id);
    }
  }
  return [...new Set(errors)];
}

export function initialSnapshot(machine: StateMachine): Record<string, string> {
  return Object.fromEntries(machine.entities.map(entity => [entity.id, entity.initial_state]));
}

/** A system target is an operation request. It never becomes a state effect. */
export function connectStates(machine: StateMachine, start: {entityId: string; stateId: string}, end: {entityId: string; stateId: string}): TransitionRule {
  const source = machine.entities.find(group => group.id === start.entityId)!;
  const target = machine.entities.find(group => group.id === end.entityId)!;
  const anchored = source.ownership === 'system' || start.entityId !== end.entityId || target.ownership === 'system';
  const rule: TransitionRule = { id: makeId('rule'), name: `${source.states.find(state => state.id === start.stateId)!.label} → ${target.states.find(state => state.id === end.stateId)!.label}`,
    enabled: anchored, trigger: {entity_id: source.id, event: anchored ? 'state.entered' : 'unconfigured', ...(anchored ? {state_id: start.stateId} : {})}, conditions: [], effects: [] };
  if (target.ownership === 'system') {
    const command = target.commands?.find(command => command.outcomes.includes(end.stateId));
    if (!command) throw new Error('This system state has no registered command. It can be observed as a source.');
    rule.command = {entity_id: target.id, state_id: end.stateId, command_id: command.id, arguments: command.kind === 'run' ? {prompt: ''} : {}};
  } else rule.effects = [{entity_id: target.id, from_state: start.entityId === end.entityId ? start.stateId : '*', to_state: end.stateId}];
  return rule;
}

export function removeState(machine: StateMachine, entityId: string, stateId: string): StateMachine {
  const entity = machine.entities.find(item => item.id === entityId);
  if (!entity || entity.ownership === 'system' || entity.states.length <= 1 || !entity.states.some(state => state.id === stateId)) return machine;
  const states = entity.states.filter(state => state.id !== stateId);
  return { ...machine,
    entities: machine.entities.map(item => item.id === entityId ? { ...item, states,
      initial_state: item.initial_state === stateId ? states[0].id : item.initial_state } : item),
    rules: machine.rules.filter(rule => !rule.conditions.some(condition => condition.entity_id === entityId && condition.state_id === stateId)
      && !(rule.trigger.entity_id === entityId && rule.trigger.state_id === stateId)
      && !(rule.command?.entity_id === entityId && rule.command.state_id === stateId)
      && !rule.program?.signals.some(signal => signal.match.entity_id === entityId && signal.match.state_id === stateId)
      && !rule.effects.some(effect => effect.entity_id === entityId && (effect.from_state === stateId || effect.to_state === stateId))
      && !(rule.program && expressionReferences(rule.program.expression).states.some(state => state.entity_id === entityId && state.state_id === stateId))),
  };
}

export function removeEntity(machine: StateMachine, id: string): StateMachine {
  if (machine.entities.some(entity => entity.id === id && entity.ownership === 'system')) return machine;
  const removed = new Set([id]);
  let previousSize = 0;
  while (removed.size !== previousSize) {
    previousSize = removed.size;
    for (const entity of machine.entities) if (entity.parent_id && removed.has(entity.parent_id)) removed.add(entity.id);
  }
  if (machine.entities.some(entity => removed.has(entity.id) && entity.ownership === 'system')) return machine;
  return { ...machine,
    ...(machine.status_entity_id && removed.has(machine.status_entity_id) ? {status_entity_id: machine.entities.find(entity => !removed.has(entity.id))?.id} : {}),
    entities: machine.entities.filter(entity => !removed.has(entity.id)),
    rules: machine.rules.filter(rule => !removed.has(rule.trigger.entity_id)
      && !(rule.command && removed.has(rule.command.entity_id))
      && !rule.conditions.some(condition => removed.has(condition.entity_id))
      && !rule.effects.some(effect => removed.has(effect.entity_id))
      && !(rule.program && (rule.program.signals.some(signal => removed.has(signal.match.entity_id))
        || expressionReferences(rule.program.expression).states.some(state => removed.has(state.entity_id))))),
  };
}
