import type { MachineEntity, MachineState, StateMachine } from './model';

export const STATE_SIZE = 104;
const gridPosition = (slot: number) => ({ x: 65 + slot % 2 * 220, y: 110 + Math.floor(slot / 2) * 210 });

export function overlapsStatePosition(states: Pick<MachineState, 'position'>[], position: MachineState['position']) {
  return states.some(state => Math.abs(state.position.x - position.x) < 150 && Math.abs(state.position.y - position.y) < 170);
}

/** Positions are local to the owning Agent/object, shared by all its groups. */
export function nextStatePosition(states: Pick<MachineState, 'position'>[], preferred = gridPosition(0)) {
  let position = preferred, slot = 0;
  while (overlapsStatePosition(states, position)) {
    position = gridPosition(slot++);
  }
  return position;
}

/** A SYSTEM group is part of its owner's space, never a sibling machine. */
export function machineSpaces(machine: StateMachine) {
  const owners = new Map<string, MachineEntity[]>();
  for (const entity of machine.entities) {
    const owner = entity.card_id ?? '__owner__';
    const entities = owners.get(owner) ?? [];
    entities.push(entity); owners.set(owner, entities);
  }
  return [...owners].map(([id, entities]) => ({ id, entities,
    width: Math.max(320, ...entities.flatMap(entity => entity.states.map(state => state.position.x + STATE_SIZE + 70))),
    height: Math.max(280, ...entities.flatMap(entity => entity.states.map(state => state.position.y + STATE_SIZE + 110))),
  }));
}
