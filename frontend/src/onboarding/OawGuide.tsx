import { useLayoutEffect, useId, useRef, type RefObject } from 'react';
import canonicalLogo from '../../../docs/assets/logo.svg?raw';
import { GuideRigMachine, REST, rigGeometry, type GuideMotion, type RigPose } from './guideRig';

export type { GuideMotion } from './guideRig';
function part(pattern: RegExp) {
  const value = canonicalLogo.match(pattern)?.[0];
  if (!value) throw new Error('The OAW logo structure changed; update its guide artwork extraction.');
  return value;
}
const definitions = part(/<defs>[\s\S]*?<\/defs>/);
const profile = part(/<path\b[\s\S]*?\/>/);
const head = part(/<circle[^>]+r="81"[^>]*\/>/);
const ring = part(/<circle[^>]+r="449"[^>]*\/>/);

const headX = Number(head.match(/cx="([\d.]+)"/)![1]);
const headY = Number(head.match(/cy="([\d.]+)"/)![1]);
const headRadius = Number(head.match(/r="([\d.]+)"/)![1]);
const ink = head.match(/fill="([^"]+)"/)![1];
const scale = headRadius / 9;
const rest = rigGeometry(REST);

/** Bind once; both the live guide and deterministic motion previews use this renderer. */
export function bindGuideRig(svg: SVGSVGElement) {
  const get = <T extends SVGElement>(name: string) => svg.querySelector<T>(`[data-rig="${name}"]`)!;
  const profileArt = get('profile'), body = get('front'), root = get('root');
  const left = get('left-leg'), right = get('right-leg'), torso = get('torso'), headArt = get('head'), shadow = get('shadow');
  const leftBones = get('left-bones'), rightBones = get('right-bones');
  return (pose: RigPose, turn = 1) => {
    const rig = rigGeometry(pose);
    profileArt.setAttribute('opacity', String(1 - turn));
    body.setAttribute('opacity', String(turn));
    root.setAttribute('transform', `translate(0 ${rig.lift})`);
    left.setAttribute('d', rig.leftPath); right.setAttribute('d', rig.rightPath); torso.setAttribute('d', rig.torsoPath);
    // One circular head travels from the logo's profile into the standing rig.
    headArt.setAttribute('cx', String(headX + (627 + rig.head.x * scale - headX) * turn));
    headArt.setAttribute('cy', String(headY + (1070 + (rig.head.y + rig.lift) * scale - headY) * turn));
    shadow.setAttribute('opacity', String(turn * Math.max(.025, .09 + rig.lift * .0017)));
    shadow.setAttribute('rx', String(26 * Math.max(.45, 1 + rig.lift / 65)));
    for (const [element, bones] of [[leftBones, rig.left], [rightBones, rig.right]] as const) {
      element.setAttribute('d', `M${bones.hip.x} ${bones.hip.y} L${bones.knee.x} ${bones.knee.y} L${bones.foot.x} ${bones.foot.y}`);
    }
  };
}

export function OawGuide({ motion = 'idle', ringOnly = false, logo = false, inLogo = false,
  movementTarget, celebration = 0, debug = false }: {
  motion?: GuideMotion; ringOnly?: boolean; logo?: boolean; inLogo?: boolean;
  movementTarget?: RefObject<HTMLElement>; celebration?: number; debug?: boolean;
}) {
  const id = useId().replace(/:/g, '');
  const svg = useRef<SVGSVGElement>(null);
  const machine = useRef<GuideRigMachine>();
  const wasLogo = useRef(inLogo || logo);
  const jumpSeen = useRef(celebration);
  const current = useRef({ motion, movementTarget, celebration });
  current.current = { motion, movementTarget, celebration };
  const scope = (markup: string) => markup.replace(/(ring-blue|head-opening|body-boundary)/g, `$1-${id}`);

  useLayoutEffect(() => {
    if (ringOnly) return;
    const rig = machine.current ??= new GuideRigMachine();
    const paint = bindGuideRig(svg.current!);
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    rig.setReducedMotion(media.matches);
    if (inLogo || logo) { wasLogo.current = true; paint(REST, 0); return; }
    if (wasLogo.current || motion === 'enter') rig.enter();
    wasLogo.current = false;
    let frame = 0, last = 0, previous: { x: number; y: number } | undefined;
    let direction = 1;
    let previousMotion: GuideMotion = 'idle';
    const changedMotion = () => {
      cancelAnimationFrame(frame);
      rig.setReducedMotion(media.matches);
      last = 0; previous = undefined;
      paint(rig.pose, rig.turn);
      svg.current!.dataset.state = rig.state;
      if (!media.matches) frame = requestAnimationFrame(tick);
    };
    const visibility = () => { last = 0; previous = undefined; };
    function tick(now: number) {
      const dt = last ? Math.min((now - last) / 1000, .05) : 0;
      last = now;
      const input = current.current;
      const rect = input.movementTarget?.current?.getBoundingClientRect();
      let speed = 0;
      if (rect && previous && dt > 0) {
        const dx = rect.x - previous.x, dy = rect.y - previous.y;
        speed = Math.hypot(dx, dy) / dt / 150;
        if (Math.abs(dx) > .2) direction = Math.sign(dx);
      }
      previous = rect ? { x: rect.x, y: rect.y } : undefined;
      rig.setIntent(speed, input.motion, direction);
      if (input.celebration !== jumpSeen.current || (input.motion === 'celebrate' && previousMotion !== 'celebrate')) {
        jumpSeen.current = input.celebration; rig.jump();
      }
      previousMotion = input.motion;
      rig.update(dt);
      if (svg.current) svg.current.dataset.state = rig.state;
      paint(rig.pose, rig.turn);
      frame = requestAnimationFrame(tick);
    }
    paint(rig.pose, rig.turn);
    svg.current!.dataset.state = rig.state;
    media.addEventListener('change', changedMotion);
    document.addEventListener('visibilitychange', visibility);
    if (!media.matches) frame = requestAnimationFrame(tick);
    return () => { cancelAnimationFrame(frame); media.removeEventListener('change', changedMotion); document.removeEventListener('visibilitychange', visibility); };
    // Intent changes are consumed by the same clock without recreating the rig.
  }, [ringOnly, inLogo, logo]);

  return <svg ref={svg} viewBox="0 0 1254 1254" className="oaw-guide-art" aria-hidden="true" data-motion={motion}>
    <g dangerouslySetInnerHTML={{ __html: scope(definitions) }} />
    {!ringOnly && <>
      <g data-rig="profile" opacity={inLogo || logo ? 1 : 0} dangerouslySetInnerHTML={{ __html: scope(profile) }} />
      <g data-rig="front" opacity={inLogo || logo ? 0 : 1} transform={`translate(627 1070) scale(${scale})`} fill={ink}>
        <ellipse data-rig="shadow" cx="0" cy="6" rx="26" ry="2.2" fill="currentColor" opacity=".09" />
        <g data-rig="root">
          <path data-rig="left-leg" d={rest.leftPath} />
          <path data-rig="right-leg" d={rest.rightPath} />
          <path data-rig="torso" d={rest.torsoPath} />
          <g visibility={debug ? 'visible' : 'hidden'} fill="none" stroke="#ec8d53" strokeWidth="1" strokeLinecap="round" strokeLinejoin="round">
            <path data-rig="left-bones" /><path data-rig="right-bones" />
          </g>
        </g>
      </g>
      <circle data-rig="head" cx={inLogo || logo ? headX : 627} cy={inLogo || logo ? headY : 1070 - 77 * scale} r={headRadius} fill={ink} />
    </>}
    {(ringOnly || logo) && <g dangerouslySetInnerHTML={{ __html: scope(ring) }} />}
  </svg>;
}
