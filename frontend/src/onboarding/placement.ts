export interface GuideRect { x: number; y: number; width: number; height: number }
const overlap = (a: GuideRect, b: GuideRect) => Math.max(0, Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x))
  * Math.max(0, Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y));

/** Find nearby empty canvas without moving any existing cards. */
export function vacantPosition(desired: GuideRect, obstacles: GuideRect[], clearance = 32) {
  const padded = obstacles.map(rect => ({ x: rect.x - clearance, y: rect.y - clearance, width: rect.width + clearance * 2, height: rect.height + clearance * 2 }));
  if (!padded.some(rect => overlap(desired, rect))) return { x: desired.x, y: desired.y };
  const xs = [desired.x, ...padded.flatMap(rect => [rect.x - desired.width, rect.x + rect.width])];
  const ys = [desired.y, ...padded.flatMap(rect => [rect.y - desired.height, rect.y + rect.height])];
  let best = { x: Math.min(...xs), y: desired.y }, distance = Infinity;
  for (const x of xs) for (const y of ys) {
    const nextDistance = Math.hypot(x - desired.x, y - desired.y);
    if (nextDistance < distance && !padded.some(rect => overlap({ ...desired, x, y }, rect) > .001)) {
      best = { x, y }; distance = nextDistance;
    }
  }
  return best;
}

/** Keep the interactive bubble off cards and controls; the character is transparent to input. */
export function placeGuide(desired: { x: number; y: number }, subject: GuideRect | undefined,
  bubble: { width: number; height: number }, viewport: { width: number; height: number }, obstacles: GuideRect[]) {
  const { width, height } = viewport;
  const maxX = Math.max(12, width - bubble.width - 12);
  const minY = bubble.height + 28;
  const maxY = Math.max(minY, height - 115);
  const clamp = (point: { x: number; y: number }) => ({ x: Math.max(12, Math.min(maxX, point.x)), y: Math.max(minY, Math.min(maxY, point.y)) });
  const candidates = [desired,
    ...(subject ? [
      { x: subject.x - bubble.width - 20, y: desired.y },
      { x: subject.x + subject.width + 20, y: desired.y },
      { x: subject.x + subject.width / 2 - bubble.width / 2, y: subject.y - 16 },
      { x: subject.x + subject.width / 2 - bubble.width / 2, y: subject.y + subject.height + bubble.height + 24 },
    ] : []),
    ...[12, maxX].flatMap(x => [minY, height * .55, maxY].map(y => ({ x, y }))),
  ].map(clamp);
  const score = (point: { x: number; y: number }) => {
    const rect = { x: point.x - 8, y: point.y - bubble.height - 17, width: bubble.width + 16, height: bubble.height + 16 };
    return obstacles.reduce((sum, obstacle) => sum + overlap(rect, obstacle) * 10, 0) + Math.hypot(point.x - desired.x, point.y - desired.y);
  };
  return candidates.reduce((best, point) => score(point) < score(best) ? point : best);
}
