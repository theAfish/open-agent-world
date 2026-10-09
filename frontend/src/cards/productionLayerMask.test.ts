import { describe, expect, it } from 'vitest';
import { productionMaskValue } from './productionLayerMask';

describe('custom process plate coverage', () => {
  it('separates PNG alpha from greyscale and respects transparency for both', () => {
    expect(productionMaskValue(0, 0, 0, 255, 'alpha', false)).toBe(1);
    expect(productionMaskValue(0, 0, 0, 255, 'luminance', false)).toBe(0);
    expect(productionMaskValue(255, 255, 255, 128, 'luminance', false)).toBeCloseTo(128 / 255);
    expect(productionMaskValue(255, 255, 255, 0, 'luminance', false)).toBe(0);
    expect(productionMaskValue(255, 0, 0, 255, 'luminance', false)).toBeCloseTo(.2126);
  });
  it('inverts final coverage instead of RGB alone', () => {
    expect(productionMaskValue(255, 255, 255, 0, 'luminance', true)).toBe(1);
    expect(productionMaskValue(0, 0, 0, 255, 'alpha', true)).toBe(0);
    expect(productionMaskValue(100, 150, 200, 128, 'luminance', true))
      .toBeCloseTo(1 - productionMaskValue(100, 150, 200, 128, 'luminance', false));
  });
});
