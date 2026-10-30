import { expect, it } from 'vitest';
import { libraryGroupOutline, placeLibraryGroups } from './libraryGroupLayout';

const size = { width: 600, cardWidth: 96, cardGap: 24, groupGap: 48, rowGap: 32 };

it('keeps a shared row baseline when a new group joins a wrapped group', () => {
  const { positions: [large, medium, single] } = placeLibraryGroups([Array(6).fill(134), Array(3).fill(134), [134]], size);
  expect(large).toEqual([
    { left: 0, top: 0 }, { left: 120, top: 0 }, { left: 240, top: 0 },
    { left: 360, top: 0 }, { left: 480, top: 0 }, { left: 0, top: 190 },
  ]);
  expect(medium).toEqual([{ left: 144, top: 190 }, { left: 264, top: 190 }, { left: 384, top: 190 }]);
  expect(single).toEqual([{ left: 0, top: 380 }]);
});

it('starts a new row when wrapping would disconnect a group outline', () => {
  const { positions: [, next] } = placeLibraryGroups([Array(2).fill(134), Array(3).fill(134)], { ...size, width: 456 });
  expect(next).toEqual([{ left: 0, top: 190 }, { left: 120, top: 190 }, { left: 240, top: 190 }]);
  expect(placeLibraryGroups([[134, 134], [134]], { ...size, width: 96 }).positions).toEqual([
    [{ left: 0, top: 0 }, { left: 0, top: 166 }], [{ left: 0, top: 356 }],
  ]);
});

it('respects taller internal cards when wrapping to the next row', () => {
  const layout = placeLibraryGroups([[134, 164, 134, 134]], { ...size, width: 336 });
  expect(layout.positions[0][3]).toEqual({ left: 0, top: 196 });
  expect(layout.height).toBe(330);
});

it('joins unequal row runs into one closed outline with a concave turn', () => {
  const path = libraryGroupOutline([
    { left: 0, top: 0, right: 500, bottom: 150 },
    { left: 0, top: 190, right: 100, bottom: 340 },
  ], 0);
  expect(path.match(/M /g)).toHaveLength(1);
  expect(path).toContain('500 150');
  expect(path).toContain('100 150');
  expect(path).toContain('100 340');
  expect(path.endsWith(' Z')).toBe(true);
  expect(libraryGroupOutline([])).toBe('');
});

it('joins the tab and wrapped body into one outline without an internal divider', () => {
  const path = libraryGroupOutline([
    { left: 0, top: 24, right: 500, bottom: 174 },
    { left: 0, top: 214, right: 100, bottom: 364 },
  ], 0, 120);
  expect(path.match(/M /g)).toHaveLength(1);
  expect(path).toContain('120 0');
  expect(path).toContain('120 24');
  expect(path).not.toContain(' L 0 24');
  expect(path).toContain('100 364');
  expect(path.endsWith(' Z')).toBe(true);
});
