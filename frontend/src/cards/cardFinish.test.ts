import { describe, expect, it } from 'vitest';
import { CARD_FINISHES, finishLabel, normalizeCardFinish } from './cardFinish';

describe('stored finish compatibility', () => {
  it.each(CARD_FINISHES)('preserves %s across serialization', finish => {
    expect(normalizeCardFinish(JSON.parse(JSON.stringify({ finish })).finish)).toBe(finish);
    expect(finishLabel(finish).toLowerCase()).toBe(finish);
  });
  it.each([undefined, null, '', 'unknown', ['foil'], 1])('defaults unknown or old data to normal: %s', value => {
    expect(normalizeCardFinish(value)).toBe('normal');
  });
});
