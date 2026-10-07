import type { StateMachine, TransitionRule } from './model';

export interface StateConnection {
  id: string;
  rule: TransitionRule;
  source: { entityId: string; stateId: string };
  target: { entityId: string; stateId: string };
  command: boolean;
}

/** A view of visible state endpoints only. Wildcards never become graph objects. */
export function stateConnections(machine: StateMachine): StateConnection[] {
  const connections: StateConnection[] = [];
  const canonicalPairs = new Set<string>();
  for (const rule of machine.rules) {
    const effects = [...rule.effects.map(effect => ({effect, command: false})),
      ...(rule.command ? [{effect: {entity_id: rule.command.entity_id, from_state: '*', to_state: rule.command.state_id}, command: true}] : [])];
    effects.forEach(({effect, command}, index) => {
      const group = machine.entities.find(entity => entity.id === effect.entity_id);
      if (!group?.states.some(state => state.id === effect.to_state)) return;
      const anchor = rule.trigger.state_id && rule.trigger.event.startsWith('state.') ? rule.trigger : undefined;
      const condition = rule.conditions.find(item => item.entity_id !== effect.entity_id);
      const sources = anchor ? [{entityId: anchor.entity_id, stateId: anchor.state_id!}]
        : condition ? [{entityId: condition.entity_id, stateId: condition.state_id}]
        : effect.from_state !== '*' ? [{entityId: group.id, stateId: effect.from_state}]
        : group.states.filter(state => !rule.canonical || state.id !== effect.to_state).map(state => ({entityId: group.id, stateId: state.id}));
      for (const source of sources) {
        if (!machine.entities.some(entity => entity.id === source.entityId && entity.states.some(state => state.id === source.stateId))) continue;
        const target = {entityId: group.id, stateId: effect.to_state};
        const pair = JSON.stringify([source, target]);
        if (rule.canonical && canonicalPairs.has(pair)) continue;
        if (rule.canonical) canonicalPairs.add(pair);
        connections.push({id: `${rule.id}:${index}:${source.entityId}:${source.stateId}`, rule, source, target, command});
      }
    });
  }
  return connections;
}

/** Assign stable lanes to all edges between a pair, including reverse edges. */
export function connectionOffsets(connections: StateConnection[]): Map<string, number> {
  const pairs = new Map<string, StateConnection[]>();
  const address = (endpoint: StateConnection['source']) => JSON.stringify([endpoint.entityId, endpoint.stateId]);
  for (const connection of connections) {
    const key = JSON.stringify([address(connection.source), address(connection.target)].sort());
    const siblings = pairs.get(key) ?? [];
    siblings.push(connection); pairs.set(key, siblings);
  }
  const offsets = new Map<string, number>();
  for (const siblings of pairs.values()) {
    siblings.sort((a, b) => a.id.localeCompare(b.id));
    siblings.forEach((connection, index) => {
      const source = address(connection.source), target = address(connection.target);
      offsets.set(connection.id, source === target ? index * 44
        : (index - (siblings.length - 1) / 2) * 36 * (source < target ? 1 : -1));
    });
  }
  return offsets;
}
