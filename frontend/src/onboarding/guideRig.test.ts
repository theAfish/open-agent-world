import { describe, expect, it } from 'vitest';
import { GuideRigMachine, REST, rigGeometry, sampleRig, solveLeg, type RigState } from './guideRig';

const advance = (machine: GuideRigMachine, seconds: number, fps = 120) => {
  for (let index = 0; index < Math.round(seconds * fps); index++) machine.update(1 / fps);
};
const distance = (a: { x: number; y: number }, b: { x: number; y: number }) => Math.hypot(a.x - b.x, a.y - b.y);

describe('the standing OAW skeleton', () => {
  it('has mirrored legs and a centered head, including while breathing', () => {
    for (const time of [0, .4, 1.2, 2.8, 3.6]) {
      const rig = rigGeometry(sampleRig('idle', time));
      expect(rig.head.x).toBe(0);
      for (const joint of ['hip', 'knee', 'foot'] as const) {
        expect(rig.left[joint].x).toBeCloseTo(-rig.right[joint].x, 8);
        expect(rig.left[joint].y).toBeCloseTo(rig.right[joint].y, 8);
      }
    }
  });
  it('keeps bone lengths fixed and both skinned foot tips attached through every action', () => {
    for (const state of ['idle', 'walk', 'jump', 'think', 'enter', 'indicate', 'speak'] as RigState[]) {
      for (let time = 0; time < 3.2; time += .037) {
        const rig = rigGeometry(sampleRig(state, time));
        for (const [leg, path] of [[rig.left, rig.leftPath], [rig.right, rig.rightPath]] as const) {
          expect(distance(leg.hip, leg.knee)).toBeCloseTo(19, 7);
          expect(distance(leg.knee, leg.foot)).toBeCloseTo(19, 7);
          const numbers = path.match(/-?\d+\.\d+/g)!.map(Number);
          expect(numbers[6]).toBeCloseTo(leg.foot.x, 2);
          expect(numbers[7]).toBeCloseTo(leg.foot.y, 2);
          expect(numbers.every(Number.isFinite)).toBe(true);
        }
      }
    }
    expect(distance(solveLeg({ x: 0, y: 0 }, { x: 1000, y: 0 }, 1).foot, { x: 0, y: 0 })).toBeLessThan(38);
  });
  it('alternates the lifted leg with a planted opposite foot and returns from a crouched jump', () => {
    const left = rigGeometry(sampleRig('walk', .2)), right = rigGeometry(sampleRig('walk', .6));
    expect(left.left.foot.y).toBeLessThan(-8); expect(left.right.foot.y).toBeCloseTo(0);
    expect(right.right.foot.y).toBeLessThan(-8); expect(right.left.foot.y).toBeCloseTo(0);
    expect(sampleRig('jump', .17).sy).toBeLessThan(.85);
    expect(sampleRig('jump', .53).lift).toBeLessThan(-25);
    expect(sampleRig('jump', 1.05)).toEqual(REST);
  });
});

describe('guide animation intent and transitions', () => {
  it('lets an entrance and a jump finish before returning to the latest walking/thinking intent', () => {
    const machine = new GuideRigMachine();
    machine.enter(); machine.setIntent(1, 'think');
    expect(machine.jump()).toBe(false);
    advance(machine, .2); machine.enter(); expect(machine.time).toBe(0); expect(machine.turn).toBe(0);
    advance(machine, 1);
    expect(machine.state).toBe('walk'); expect(machine.turn).toBe(1);
    expect(machine.jump()).toBe(true); expect(machine.jump()).toBe(false);
    machine.setIntent(0, 'think'); advance(machine, 1.1);
    expect(machine.state).toBe('think');
  });
  it('preserves pose and velocity during rapid changes and lands at the symmetric rest pose', () => {
    const machine = new GuideRigMachine();
    let largestStep = 0;
    for (let index = 0; index < 720; index++) {
      if (index % 43 === 0) machine.setIntent(index % 86 ? 0 : 1.8, 'think', index % 129 ? 1 : -1);
      if (index % 97 === 0) {
        const before = { ...machine.pose }, velocity = { ...machine.velocity };
        machine.jump(); expect(machine.pose).toEqual(before); expect(machine.velocity).toEqual(velocity);
      }
      const before = rigGeometry(machine.pose);
      machine.update(1 / 120);
      const after = rigGeometry(machine.pose);
      largestStep = Math.max(largestStep, distance(before.left.foot, after.left.foot), distance(before.right.foot, after.right.foot));
    }
    expect(largestStep).toBeLessThan(3);
    machine.setIntent(0, 'idle'); advance(machine, 3);
    const rest = rigGeometry(machine.pose);
    expect(rest.left.foot.x).toBeCloseTo(-rest.right.foot.x, 4);
    expect(rest.left.foot.y).toBeCloseTo(0, 4);
  });
  it('has consistent timing across frame rates and respects reduced motion', () => {
    const fast = new GuideRigMachine(), slow = new GuideRigMachine();
    for (const machine of [fast, slow]) machine.setIntent(1.2, 'idle');
    advance(fast, 2, 120); advance(slow, 2, 30);
    for (const key of Object.keys(REST) as (keyof typeof REST)[]) expect(fast.pose[key]).toBeCloseTo(slow.pose[key], 6);
    fast.setReducedMotion(true); fast.setIntent(2, 'think'); advance(fast, 1);
    expect(fast.jump()).toBe(false); expect(fast.pose).toEqual(REST);
    fast.setReducedMotion(false); advance(fast, .2); expect(fast.state).toBe('walk');
    fast.setIntent(NaN, 'idle'); expect(fast.intent.speed).toBe(0);
    expect(() => fast.update(Infinity)).toThrow(RangeError);
  });
});
