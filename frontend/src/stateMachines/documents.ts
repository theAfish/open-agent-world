import type { MachineDefinition, MachineDocument, MachinePresentation } from './apiTypes';
import { readMachine, type StateMachine, type TransitionRule, type TriggerExpression } from './model';
import { nextStatePosition, overlapsStatePosition } from './graphLayout';

/** Presentation is applied only to the edit buffer, never to the stored definition. */
export function machineFromDocument(document: MachineDocument): StateMachine | null {
  if (!document.definition) return null;
  const placed: { entityId: string; position: { x: number; y: number }; moved: boolean }[] = [];
  return readMachine({ ...document.definition, entities: document.definition.entities.map(entity => ({
    ...entity, states: entity.states.map(state => {
      const saved = document.presentation.positions?.[entity.id]?.[state.id];
      // Legacy coordinates were group-local. Keep existing arrangements where
      // possible, resolving collisions across groups once when opening them.
      const available = saved && (document.presentation.coordinate_space === 'owner'
        || !overlapsStatePosition(placed.filter(item => item.entityId !== entity.id || item.moved), saved));
      const position = available ? saved : nextStatePosition(placed, saved);
      placed.push({ entityId: entity.id, position, moved: position !== saved });
      return { ...state, position };
    }),
  })) });
}

export function splitMachine(machine: StateMachine, presentation: MachinePresentation = {}): { definition: MachineDefinition; presentation: MachinePresentation } {
  return {
    definition: { ...machine, entities: machine.entities.map(entity => ({ ...entity, states: entity.states.map(({ id, label }) => ({ id, label })) })) },
    presentation: { viewports: presentation.viewports, expanded: presentation.expanded, coordinate_space: 'owner', positions: Object.fromEntries(machine.entities.map(entity => [entity.id,
      Object.fromEntries(entity.states.map(state => [state.id, state.position])),
    ])) },
  };
}

export interface MachineEntry { cardId: string; machine: StateMachine; version: number }
type References = NonNullable<StateMachine['references']>;
export interface MachineView {
  machine: StateMachine;
  entityOwners: Record<string, { cardId: string; entityId: string }>;
  ruleOwners: Record<string, { cardId: string; ruleId: string }>;
  /** Persistent references belong to the active owner, not the projected graph. */
  references: References;
  entityIds: Record<string, Record<string, string>>;
  versions: Record<string, number>;
}

const identity = (cardId: string, entityId: string) => JSON.stringify([cardId, entityId]);

function viewId(prefix: string, cardId: string, originalId: string, used: Set<string>): string {
  const source = identity(cardId, originalId);
  // The host bounds identifiers to 128 characters. Keep long plugin IDs bounded
  // and resolve even hash/user-selected identifier collisions in this view.
  let hash = 2166136261;
  for (let index = 0; index < source.length; index++) hash = Math.imul(hash ^ source.charCodeAt(index), 16777619);
  const readable = `${prefix}:${encodeURIComponent(cardId)}:${encodeURIComponent(originalId)}`;
  const base = readable.length <= 116 ? readable : `${prefix}:${(hash >>> 0).toString(36)}`;
  let result = base, suffix = 1;
  while (used.has(result)) result = `${base}:${suffix++}`;
  used.add(result);
  return result;
}

function remapExpression(expression: TriggerExpression, entityId: (id: string) => string): TriggerExpression {
  if (expression.op === 'state') return { ...expression, entity_id: entityId(expression.entity_id) };
  if ('left' in expression) return { ...expression, left: remapExpression(expression.left, entityId), right: remapExpression(expression.right, entityId) };
  if ('args' in expression) return { ...expression, args: expression.args.map(value => remapExpression(value, entityId)) };
  if ('arg' in expression) return { ...expression, arg: remapExpression(expression.arg, entityId) };
  return { ...expression };
}

/** Remap schema references only; plugin action arguments and world object IDs stay opaque. */
export function remapRule(rule: TransitionRule, entityId: (id: string) => string, ruleId = rule.id): TransitionRule {
  const copy = structuredClone(rule);
  return { ...copy, id: ruleId, trigger: { ...copy.trigger, entity_id: entityId(copy.trigger.entity_id) },
    conditions: copy.conditions.map(condition => ({ ...condition, entity_id: entityId(condition.entity_id) })),
    effects: copy.effects.map(effect => ({ ...effect, entity_id: entityId(effect.entity_id) })),
    ...(copy.command ? { command: { ...copy.command, entity_id: entityId(copy.command.entity_id) } } : {}),
    ...(copy.program ? { program: { ...copy.program,
      signals: copy.program.signals.map(signal => ({ ...signal, match: { ...signal.match, entity_id: entityId(signal.match.entity_id) } })),
      expression: remapExpression(copy.program.expression, entityId),
    } } : {}),
  };
}

/** Project only loaded documents. Template-local IDs never identify another owner. */
export function projectView(activeId: string, entries: MachineEntry[]): MachineView {
  const documents = new Map(entries.map(entry => [entry.cardId, entry]));
  const active = documents.get(activeId);
  if (!active) throw new Error('The active state-machine document is not loaded.');
  const ordered = [active, ...[...documents.values()].filter(entry => entry.cardId !== activeId).sort((a, b) => a.cardId.localeCompare(b.cardId))];
  const references: References = structuredClone(active.machine.references ?? []);
  const used = new Set([...active.machine.entities.map(entity => entity.id), ...references.map(reference => reference.entity_id)]);
  const groups = new Map<string, string>();
  const entityOwners: MachineView['entityOwners'] = {}, ruleOwners: MachineView['ruleOwners'] = {}, entityIds: MachineView['entityIds'] = {};
  for (const entity of active.machine.entities) groups.set(identity(activeId, entity.id), entity.id);
  for (const reference of references) {
    const groupId = reference.state_group_id ?? documents.get(reference.card_id)?.machine.entities[0]?.id;
    if (groupId) groups.set(identity(reference.card_id, groupId), reference.entity_id);
  }
  const ensure = (cardId: string, groupId: string, version?: number): string => {
    const key = identity(cardId, groupId);
    const existing = groups.get(key);
    if (existing) return existing;
    const alias = viewId('ref', cardId, groupId, used);
    groups.set(key, alias);
    references.push({ entity_id: alias, card_id: cardId, state_group_id: groupId,
      ...(version ? { definition_version: version } : {}) });
    return alias;
  };
  for (const entry of ordered) {
    const mapping: Record<string, string> = {};
    for (const entity of entry.machine.entities) {
      const alias = ensure(entry.cardId, entity.id, entry.version);
      mapping[entity.id] = alias;
      entityOwners[alias] = { cardId: entry.cardId, entityId: entity.id };
    }
    entityIds[entry.cardId] = mapping;
  }
  // An expanded member can itself refer to a different loaded/unloaded owner.
  // Reuse the same object/group address rather than nesting a copied graph.
  for (const entry of ordered) for (const reference of entry.machine.references ?? []) {
    const groupId = reference.state_group_id ?? documents.get(reference.card_id)?.machine.entities[0]?.id;
    const alias = groupId ? ensure(reference.card_id, groupId, reference.definition_version)
      : entry.cardId === activeId ? reference.entity_id : viewId('ref', entry.cardId, reference.entity_id, used);
    entityIds[entry.cardId][reference.entity_id] = alias;
    if (!groupId && entry.cardId !== activeId) references.push({ ...reference, entity_id: alias });
  }
  const ruleIds = new Set(active.machine.rules.map(rule => rule.id));
  const entities: StateMachine['entities'] = [], rules: TransitionRule[] = [];
  for (const entry of ordered) {
    const mapId = (id: string) => entityIds[entry.cardId][id] ?? id;
    for (const entity of entry.machine.entities) entities.push({ ...structuredClone(entity), id: mapId(entity.id),
      card_id: entry.cardId, ...(entity.parent_id ? { parent_id: mapId(entity.parent_id) } : {}) });
    for (const rule of entry.machine.rules) {
      const id = entry.cardId === activeId ? rule.id : viewId('rule', entry.cardId, rule.id, ruleIds);
      ruleOwners[id] = { cardId: entry.cardId, ruleId: rule.id };
      rules.push(remapRule(rule, mapId, id));
    }
  }
  const loadedIds = new Set(entities.map(entity => entity.id));
  const unresolved = references.filter(reference => !loadedIds.has(reference.entity_id));
  return { machine: { version: 2, entities, rules, ...(unresolved.length ? { references: unresolved } : {}) },
    entityOwners, ruleOwners, references, entityIds,
    versions: Object.fromEntries(ordered.map(entry => [entry.cardId, entry.version])) };
}

/** Translate an edited view rule back to its one authoritative owner's namespace. */
export function ruleForOwner(view: MachineView, owner: MachineEntry, rule: TransitionRule): { rule: TransitionRule; references: References } {
  const references = structuredClone(owner.machine.references ?? []);
  const used = new Set([...owner.machine.entities.map(entity => entity.id), ...references.map(reference => reference.entity_id)]);
  const reverse = new Map(Object.entries(view.entityIds[owner.cardId] ?? {}).map(([local, displayed]) => [displayed, local]));
  const mapId = (displayed: string) => {
    const existing = reverse.get(displayed);
    if (existing) return existing;
    const target = view.entityOwners[displayed];
    if (!target) return displayed;
    const alias = viewId('ref', target.cardId, target.entityId, used);
    const source = view.references.find(reference => reference.card_id === target.cardId && reference.state_group_id === target.entityId);
    const version = source?.definition_version || view.versions[target.cardId];
    references.push({ entity_id: alias, card_id: target.cardId, state_group_id: target.entityId,
      ...(version ? { definition_version: version } : {}) });
    reverse.set(displayed, alias);
    return alias;
  };
  const identity = view.ruleOwners[rule.id];
  return { rule: remapRule(rule, mapId, identity?.cardId === owner.cardId ? identity.ruleId : rule.id), references };
}

/** Referenced members remain separate authoritative documents, including their rules. */
export function withReferences(machine: StateMachine, referenced: MachineEntry[]): StateMachine {
  const ownerId = machine.entities.find(entity => entity.card_id)?.card_id ?? '__current_owner__';
  const view = projectView(ownerId, [{ cardId: ownerId, machine, version: 0 }, ...referenced.filter(entry => entry.cardId !== ownerId)]);
  return { ...machine, ...(view.references.length ? { references: view.references } : {}) };
}
