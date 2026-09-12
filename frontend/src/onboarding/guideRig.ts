/** Front-facing OAW rig, adapted from the .tmp motion study.
 * The two existing tapered legs are skinned to hip/knee/foot chains; joints are
 * controls, not extra visible anatomy. World movement belongs to the host. */
export type GuideMotion = 'enter' | 'idle' | 'walk' | 'indicate' | 'celebrate' | 'think' | 'speak';
export type RigState = 'enter' | 'idle' | 'walk' | 'jump' | 'think' | 'indicate' | 'speak';
export interface Point { x: number; y: number }
export interface RigPose {
  lift: number; bob: number; lean: number; sx: number; sy: number;
  headX: number; headY: number;
  leftX: number; leftY: number; rightX: number; rightY: number;
}
export const REST: Readonly<RigPose> = Object.freeze({
  lift: 0, bob: 0, lean: 0, sx: 1, sy: 1, headX: 0, headY: 0,
  leftX: -27, leftY: 0, rightX: 27, rightY: 0,
});
export const RIG_TIMING = { enter: .95, jump: 1.05, walk: .8, think: 3.2 } as const;
const TAU = Math.PI * 2;
const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));
export const smooth = (value: number) => { const t = clamp(value, 0, 1); return t * t * (3 - 2 * t); };
const pose = (patch: Partial<RigPose> = {}): RigPose => ({ ...REST, ...patch });
const channels = Object.keys(REST) as (keyof RigPose)[];
function keyframes(time: number, frames: [number, Partial<RigPose>][]) {
  for (let index = 1; index < frames.length; index++) {
    if (time > frames[index][0]) continue;
    const [start, a] = frames[index - 1], [end, b] = frames[index];
    const first = pose(a), last = pose(b), t = smooth((time - start) / (end - start));
    return Object.fromEntries(channels.map(key => [key, first[key] + (last[key] - first[key]) * t])) as unknown as RigPose;
  }
  return pose(frames.at(-1)![1]);
}

export function sampleRig(state: RigState, time: number, direction = 1): RigPose {
  if (state === 'jump') return keyframes(time, [
    [0, {}], [.17, { sy: .78, sx: 1.13, headY: 2, leftX: -29, rightX: 29 }],
    [.32, { lift: -17, sy: 1.09, sx: .94, leftX: -21, rightX: 21 }],
    [.53, { lift: -32, leftX: -18, rightX: 18, leftY: -4, rightY: -4 }],
    [.72, { lift: -8, sy: 1.06, sx: .97, leftX: -24, rightX: 24 }],
    [.82, { sy: .79, sx: 1.14, headY: 2, leftX: -29, rightX: 29 }], [1.05, {}],
  ]);
  if (state === 'walk' || state === 'enter') {
    const phase = TAU * time / RIG_TIMING.walk;
    const left = Math.max(0, Math.sin(phase)), right = Math.max(0, -Math.sin(phase));
    return pose({ bob: -2 - 2 * Math.cos(phase * 2), lean: direction * 3 + 2 * Math.sin(phase),
      headX: direction * 1.3, headY: -.6 * Math.cos(phase * 2),
      leftX: -27 + 20 * left + 8 * right, leftY: -11 * left,
      rightX: 27 - 20 * right - 8 * left, rightY: -11 * right });
  }
  if (state === 'think') return keyframes(time % RIG_TIMING.think, [
    [0, {}], [.45, { lean: -9, headX: -3, sy: .96, rightY: -3 }],
    [1.05, { lean: -9, headX: -3, sy: .96, rightY: -3 }],
    [1.3, { lean: -8, headX: -3, rightY: 0 }], [1.5, { lean: -9, headX: -3, rightY: -5 }],
    [1.75, { lean: -8, headX: -3 }], [2.2, { lean: 5, headX: 2, headY: -1 }],
    [2.7, { lean: 5, headX: 2, headY: -1 }], [3.2, {}],
  ]);
  if (state === 'indicate') {
    const attention = Math.pow(Math.sin(Math.PI * (time % 2.8) / 2.8), 2);
    return pose({ lean: direction * 6 * attention, headX: direction * 2.5 * attention });
  }
  if (state === 'speak') return pose({ sy: 1 + .007 * Math.sin(TAU * time / 2), headY: -1.1 * Math.pow(Math.sin(TAU * time / 1.3), 2) });
  // Rest keeps bilateral symmetry, including the head. Breathing does not sway.
  return pose({ sy: 1 + .009 * Math.sin(TAU * time / 3.6), headY: -.3 * Math.sin(TAU * time / 3.6) });
}

/** A small intent machine with one clock and retained pose/velocity on changes. */
export class GuideRigMachine {
  state: RigState = 'idle';
  time = 0;
  pose: RigPose = pose();
  velocity = Object.fromEntries(channels.map(key => [key, 0])) as unknown as RigPose;
  turn = 1;
  reducedMotion = false;
  intent = { speed: 0, motion: 'idle' as GuideMotion, direction: 1 };

  setIntent(speed: number, motion: GuideMotion, direction = 1) {
    this.intent = { speed: Number.isFinite(speed) ? clamp(speed, 0, 2) : 0, motion,
      direction: Number.isFinite(direction) ? clamp(direction, -1, 1) : 1 };
  }
  enter() { if (!this.reducedMotion) { this.state = 'enter'; this.time = 0; this.turn = 0; } }
  jump() {
    if (this.reducedMotion || this.state === 'enter' || this.state === 'jump') return false;
    this.change('jump'); return true;
  }
  setReducedMotion(value: boolean) {
    this.reducedMotion = value;
    if (value) { this.change('idle'); this.pose = pose(); this.turn = 1; channels.forEach(key => { this.velocity[key] = 0; }); }
  }
  private change(state: RigState) { if (state !== this.state) { this.state = state; this.time = 0; } }
  private desired(): RigState {
    if (this.intent.speed > .03 || this.intent.motion === 'walk') return 'walk';
    return ['think', 'indicate', 'speak'].includes(this.intent.motion) ? this.intent.motion as RigState : 'idle';
  }
  update(dt: number) {
    if (!Number.isFinite(dt) || dt < 0) throw new RangeError('Animation dt must be finite and nonnegative');
    if (this.reducedMotion) return this.pose;
    // Bounded substeps also avoid a large leap after returning to a hidden tab.
    let remaining = Math.min(dt, .1);
    while (remaining > 1e-8) {
      const h = Math.min(remaining, 1 / 120); remaining -= h;
      if (this.state !== 'enter' && this.state !== 'jump') this.change(this.desired());
      this.time += h * (this.state === 'walk' ? .65 + .55 * Math.max(.6, this.intent.speed) : 1);
      if (this.state === 'enter') this.turn = smooth(this.time / .65);
      if ((this.state === 'enter' || this.state === 'jump') && this.time >= RIG_TIMING[this.state]) {
        this.turn = 1; this.change(this.desired());
      }
      const target = sampleRig(this.state, this.time, this.intent.direction);
      for (const key of channels) {
        const rate = key.startsWith('left') || key.startsWith('right') ? 26 : 34;
        const decay = Math.exp(-rate * h), delta = this.pose[key] - target[key];
        const c = this.velocity[key] + rate * delta;
        this.pose[key] = target[key] + (delta + c * h) * decay;
        this.velocity[key] = (this.velocity[key] - rate * c * h) * decay;
      }
    }
    return this.pose;
  }
}

export interface LegBones { hip: Point; knee: Point; foot: Point }
const UPPER = 19, LOWER = 19;
/** Two-bone IK with mirrored knee bend. Fixed bone lengths survive every pose. */
export function solveLeg(hip: Point, target: Point, side: -1 | 1): LegBones {
  const dx = target.x - hip.x, dy = target.y - hip.y;
  const length = Math.hypot(dx, dy), distance = clamp(length, .01, UPPER + LOWER - .001);
  const unit = length > .0001 ? { x: dx / length, y: dy / length } : { x: 0, y: 1 };
  const along = (UPPER * UPPER - LOWER * LOWER + distance * distance) / (2 * distance);
  const out = Math.sqrt(Math.max(0, UPPER * UPPER - along * along));
  return { hip, knee: { x: hip.x + unit.x * along + side * unit.y * out, y: hip.y + unit.y * along - side * unit.x * out },
    foot: { x: hip.x + unit.x * distance, y: hip.y + unit.y * distance } };
}
function bodyPoint(point: Point, p: RigPose): Point {
  const angle = p.lean * Math.PI / 180;
  const x = point.x * p.sx, y = point.y * p.sy + 30;
  return { x: x * Math.cos(angle) - y * Math.sin(angle), y: x * Math.sin(angle) + y * Math.cos(angle) - 30 + p.bob };
}
function onBone(point: Point, restA: Point, restB: Point, a: Point, b: Point): Point {
  const angle = Math.atan2(b.y - a.y, b.x - a.x) - Math.atan2(restB.y - restA.y, restB.x - restA.x);
  const x = point.x - restA.x, y = point.y - restA.y;
  return { x: a.x + x * Math.cos(angle) - y * Math.sin(angle), y: a.y + x * Math.sin(angle) + y * Math.cos(angle) };
}
// Cubic boundary controls from the symmetric reference. Weights bind each
// control to pelvis, thigh and shin; both legs use the same mirrored mesh.
const LEG_SKIN = [
  [16, -33, 1, 0, 0], [21, -23, .15, .85, 0], [27, -7, 0, 0, 1], [27, 0, 0, 0, 1],
  [16.2, -10, 0, .2, .8], [9, -18, .6, .4, 0], [0, -18, 1, 0, 0], [-1, -35, 1, 0, 0],
] as const;
const pointText = (p: Point) => `${p.x.toFixed(3)} ${p.y.toFixed(3)}`;
function skinnedLeg(side: -1 | 1, bones: LegBones, p: RigPose) {
  const rest = solveLeg({ x: side * 11, y: -30 }, { x: side * 27, y: 0 }, side);
  const points = LEG_SKIN.map(([x, y, pelvis, thigh, shin]) => {
    const point = { x: side * x, y };
    const a = bodyPoint(point, p), b = onBone(point, rest.hip, rest.knee, bones.hip, bones.knee), c = onBone(point, rest.knee, rest.foot, bones.knee, bones.foot);
    return pointText({ x: a.x * pelvis + b.x * thigh + c.x * shin, y: a.y * pelvis + b.y * thigh + c.y * shin });
  });
  return `M${points[0]} C${points[1]} ${points[2]} ${points[3]} C${points[4]} ${points[5]} ${points[6]} L${points[7]}Z`;
}
export function rigGeometry(p: RigPose) {
  const left = solveLeg(bodyPoint({ x: -11, y: -30 }, p), { x: p.leftX, y: p.leftY }, -1);
  const right = solveLeg(bodyPoint({ x: 11, y: -30 }, p), { x: p.rightX, y: p.rightY }, 1);
  const torso = [[0, -62], [-5, -62], [-11, -45], [-16, -33], [-13, -26], [0, -10], [13, -26], [16, -33], [11, -45], [5, -62], [0, -62]]
    .map(([x, y]) => pointText(bodyPoint({ x, y }, p)));
  const head = bodyPoint({ x: 0, y: -77 }, p);
  return { left, right, leftPath: skinnedLeg(-1, left, p), rightPath: skinnedLeg(1, right, p),
    torsoPath: `M${torso[0]} C${torso[1]} ${torso[2]} ${torso[3]} L${torso[4]} Q${torso[5]} ${torso[6]} L${torso[7]} C${torso[8]} ${torso[9]} ${torso[10]}Z`,
    head: { x: head.x + p.headX, y: head.y + p.headY }, lift: p.lift };
}
