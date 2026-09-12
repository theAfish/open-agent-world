import { describe, expect, it } from 'vitest';
import { placeGuide, vacantPosition, type GuideRect } from './placement';

const intersects = (a: GuideRect, b: GuideRect) => a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;

describe('guide placement', () => {
  it('keeps the bubble inside a narrow screen and off an open workspace', () => {
    const workspace = { x: 300, y: 80, width: 450, height: 440 };
    const bubble = { width: 256, height: 180 };
    const point = placeGuide({ x: 320, y: 220 }, workspace, bubble, { width: 768, height: 640 }, [workspace]);
    const rect = { x: point.x, y: point.y - bubble.height - 17, ...bubble };
    expect(rect.x).toBeGreaterThanOrEqual(0);
    expect(rect.y).toBeGreaterThanOrEqual(0);
    expect(rect.x + rect.width).toBeLessThanOrEqual(768);
    expect(rect.y + rect.height).toBeLessThanOrEqual(640);
    expect(intersects(rect, workspace)).toBe(false);
  });

  it('reserves space for a Minister composer among existing cards without moving them', () => {
    const obstacles = [{ x: 200, y: 150, width: 286, height: 156 }, { x: 520, y: 280, width: 286, height: 156 }, { x: 120, y: 410, width: 600, height: 156 }];
    const before = structuredClone(obstacles);
    const desired = { x: 300, y: 200, width: 480, height: 360 };
    const point = vacantPosition(desired, obstacles);
    expect(obstacles).toEqual(before);
    expect(obstacles.every(rect => !intersects({ ...desired, ...point }, { x: rect.x - 32, y: rect.y - 32, width: rect.width + 64, height: rect.height + 64 }))).toBe(true);
    expect(vacantPosition({ ...desired, x: -1000 }, obstacles)).toEqual({ x: -1000, y: 200 });
  });
});
