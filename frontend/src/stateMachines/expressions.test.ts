import { describe, expect, it } from 'vitest';
import {
  createProgram, EXPRESSION_LIMITS, expressionReferences, ExpressionParseError, parseExpression, printExpression,
  programSignals, readExpression, readProgram, tryParseExpression, validateExpression, validateProgram,
  type TriggerExpression, type TriggerProgram,
} from './expressions';
import { createEntity, type StateMachine } from './model';

const trigger = { entity_id: 'agent', event: 'capability.succeeded', capability: 'text.read', target_card_id: 'document' };
const stateMachine: StateMachine = { version: 2, entities: [{ ...createEntity('Agent'), id: 'agent', initial_state: 'idle',
  states: [{id: 'idle', label: 'Idle', position: {x: 0, y: 0}}] }], rules: [] };
const signals = ['A', 'B', 'C'].map(id => ({ id, label: id, match: { ...trigger } }));
const program = (source: string): TriggerProgram => ({ signals: signals.map(signal => ({ ...signal, match: { ...signal.match } })), expression: parseExpression(source), reset: 'on_match' });

describe('trigger expression DSL', () => {
  it('parses arithmetic before numeric comparison before logical conjunction', () => {
    expect(parseExpression('event(A) && count(B) + count(C) * 2 >= 5')).toEqual({ op: 'all', args: [
      { op: 'event', signal: 'A' },
      { op: 'gte', left: { op: 'add', left: { op: 'count', signal: 'B' }, right: {
        op: 'mul', left: { op: 'count', signal: 'C' }, right: { op: 'number', value: 2 },
      } }, right: { op: 'number', value: 5 } },
    ] });
  });

  it('gives && priority over || and ! priority over both', () => {
    expect(parseExpression('event(A) || event(B) && !event(C)')).toEqual({ op: 'any', args: [
      { op: 'event', signal: 'A' }, { op: 'all', args: [{ op: 'event', signal: 'B' }, { op: 'not', arg: { op: 'event', signal: 'C' } }] },
    ] });
  });

  it.each(['==', '!=', '>', '>=', '<', '<='])('supports the %s numeric comparator', symbol => {
    expect(printExpression(parseExpression(`count(A) ${symbol} 3`))).toBe(`count(A) ${symbol} 3`);
  });

  it.each([
    'count(A) >= 1 && count(B) >= 1',
    'count(C) >= 3',
    'event(A) && count(B) + count(C) >= 5',
    '(count(A) + count(B)) * 2 >= count(C) / (2 + 3)',
    'count(A) - (count(B) - count(C)) > 0',
    'count(A) / (count(B) * count(C)) > 0',
    'count(A) % (count(B) % 3) == 0',
    '(event(A) || event(B)) && !event(C)',
    'event(A) && (event(B) && event(C))',
    'all(event(A)) || any(event(B))',
    'state("agent", "idle") && count(A) >= -2',
  ])('prints a structurally identical bounded expression: %s', source => {
    const parsed = parseExpression(source);
    expect(parseExpression(printExpression(parsed))).toEqual(parsed);
  });

  it('supports finite decimals, exponent notation and unary numeric signs without JavaScript execution', () => {
    expect(parseExpression('-1.5e2 + +.5 >= -count(A)')).toEqual({ op: 'gte',
      left: { op: 'add', left: { op: 'number', value: -150 }, right: { op: 'number', value: 0.5 } },
      right: { op: 'sub', left: { op: 'number', value: 0 }, right: { op: 'count', signal: 'A' } },
    });
    expect(tryParseExpression('globalThis.alert("test")')).toHaveProperty('error');
    expect(tryParseExpression('event(A); count(B) > 0')).toHaveProperty('error');
  });

  it('keeps quoted state IDs intact and supports escaped characters', () => {
    const expression: TriggerExpression = { op: 'state', entity_id: 'agent/"one"', state_id: 'ready\\now' };
    expect(parseExpression(printExpression(expression))).toEqual(expression);
    expect(tryParseExpression('state(agent, idle)')).toHaveProperty('error');
  });

  it('reports exact zero-based positions and human line/column for malformed syntax', () => {
    const source = 'count(A) >=\n && event(B)';
    const result = tryParseExpression(source);
    expect(result).toMatchObject({ position: source.indexOf('&&'), line: 2, column: 2 });
    expect(result.error).toContain('line 2, column 2');
    expect(() => parseExpression('count(A) >=')).toThrow(ExpressionParseError);
    expect(tryParseExpression('state("agent", "idle)')).toMatchObject({ position: 15 });
  });

  it.each(['count(A)', 'event(A) + 1 > 2', 'count(A) && event(B)', '!count(A)', 'event(A) == event(B)', 'count(A) > 1 > 2'])
  ('rejects invalid result or operand types: %s', source => {
    expect(tryParseExpression(source)).toHaveProperty('error');
  });

  it('validates references separately so new signal IDs can be entered before they are configured', () => {
    const expression = parseExpression('count(Missing) > 0 && state("unknown", "idle")');
    expect(validateExpression(expression)).toEqual([]);
    expect(validateExpression(expression, { signals, entities: stateMachine.entities })).toEqual([
      'Unknown signal "Missing".', 'State check refers to missing state "unknown/idle".',
    ]);
  });

  it('enforces the exact AST depth and node limits, including negative literals', () => {
    expect(tryParseExpression(`${'!'.repeat(11)}event(A)`)).toHaveProperty('expression');
    expect(tryParseExpression(`${'!'.repeat(12)}event(A)`)).toHaveProperty('error');
    const source = ['!(count(A) > -1)', ...Array.from({ length: 41 }, () => 'count(A) > -1')].join(' || ');
    const expression = parseExpression(source);
    expect(readExpression(expression)).toEqual(expression);
    expect(readExpression({ op: 'not', arg: expression })).toBeNull();
    expect(tryParseExpression(`!(${source})`)).toHaveProperty('error');
    expect(tryParseExpression('('.repeat(1000) + 'event(A)' + ')'.repeat(1000))).toHaveProperty('error');
    expect(tryParseExpression(' '.repeat(EXPRESSION_LIMITS.source + 1))).toHaveProperty('error');
  });

  it('rejects malformed, circular, unknown, nonfinite and oversized AST payloads', () => {
    const circular: Record<string, unknown> = { op: 'not' }; circular.arg = circular;
    for (const invalid of [null, [], { op: 'eval', code: 'true' }, { op: 'number', value: Infinity },
      { op: 'number', value: 1e12 + 1 }, { op: 'event', signal: 'A', extra: true }, { op: 'event', signal: '_A' },
      { op: 'all', args: [] }, { op: 'any', args: Array.from({ length: 65 }, () => ({ op: 'event', signal: 'A' })) }, circular]) {
      expect(readExpression(invalid)).toBeNull();
    }
    expect(tryParseExpression('count(A) >= 1e309')).toHaveProperty('error');
  });
});

describe('trigger programs', () => {
  it('creates an independent simple-equivalent program with one current-event signal', () => {
    const created = createProgram(trigger);
    expect(created).toEqual({ signals: [{ id: 'A', label: 'A', match: trigger }], expression: { op: 'event', signal: 'A' }, reset: 'on_match' });
    created.signals[0].match.event = 'changed';
    expect(trigger.event).toBe('capability.succeeded');
    expect(programSignals({ program: created })).toBe(created.signals);
    expect(programSignals({})).toEqual([]);
  });

  it('normalizes optional nulls and defaults while retaining all filters', () => {
    const value = { ...program('count(A) >= 3'), reset: undefined, window_seconds: null };
    value.signals = value.signals.map(signal => ({ ...signal, label: ` ${signal.label} ` }));
    const restored = readProgram(value)!;
    expect(restored.reset).toBe('on_match');
    expect(restored).not.toHaveProperty('window_seconds');
    expect(restored.signals[0]).toEqual({ id: 'A', label: 'A', match: trigger });
    expect(validateProgram(restored, stateMachine)).toEqual([]);
  });

  it('requires unique existing signal, entity and state references and a boolean result', () => {
    const invalid = program('event(A)');
    invalid.signals[1].id = 'A';
    invalid.signals[2].match = { entity_id: 'gone', event: 'run.completed' };
    invalid.expression = parseExpression('count(B) > 0 && state("agent", "gone")');
    const errors = validateProgram(invalid, stateMachine);
    expect(errors).toContain('Signal IDs must be unique within a rule.');
    expect(errors).toContain('Unknown signal "B".');
    expect(errors).toContain('Source for signal "C" does not exist.');
    expect(errors).toContain('State check refers to missing state "agent/gone".');
    expect(readProgram({ ...createProgram(trigger), expression: { op: 'number', value: 1 } })).toBeNull();
  });

  it('matches backend limits for signal count, IDs, windows, reset and unknown fields', () => {
    const valid = createProgram(trigger);
    for (const invalid of [{ ...valid, signals: [] }, { ...valid, signals: Array.from({ length: 17 }, (_, index) => ({ id: `S${index}`, label: 'Signal', match: trigger })) },
      { ...valid, window_seconds: 0.5 }, { ...valid, window_seconds: 86401 }, { ...valid, window_seconds: NaN }, { ...valid, reset: 'always' },
      { ...valid, unknown: true }, { ...valid, signals: [{ id: 'A'.repeat(33), label: 'Signal', match: trigger }] }]) {
      expect(readProgram(invalid)).toBeNull();
    }
    expect(readProgram({ ...valid, window_seconds: 1 })).not.toBeNull();
    expect(readProgram({ ...valid, window_seconds: 86400, reset: 'manual' })).not.toBeNull();
  });

  it.each(['\n', '\r', '\r\n', '\u2028', '\u2029'])('rejects trailing line breaks in signal and world references: %j', suffix => {
    const valid = createProgram(trigger);
    const badSignal = { ...valid, signals: [{ id: `A${suffix}`, label: 'Signal', match: trigger }], expression: { op: 'event' as const, signal: `A${suffix}` } };
    expect(readProgram(badSignal)).toBeNull();
    expect(validateProgram(badSignal)).not.toEqual([]);
    expect(readExpression({ op: 'count', signal: `A${suffix}` })).toBeNull();
    expect(tryParseExpression(`event(${JSON.stringify(`A${suffix}`)})`)).toHaveProperty('error');
    for (const field of ['entity_id', 'event', 'capability', 'target_card_id'] as const) {
      expect(readProgram({ ...valid, signals: [{ ...valid.signals[0], match: { ...trigger, [field]: `value${suffix}` } }] })).toBeNull();
    }
    expect(readExpression({ op: 'state', entity_id: `agent${suffix}`, state_id: 'idle' })).toBeNull();
    expect(readExpression({ op: 'state', entity_id: 'agent', state_id: `idle${suffix}` })).toBeNull();
  });

  it('finds references throughout nested arithmetic and boolean trees for cascade deletion', () => {
    expect(expressionReferences(parseExpression('count(A) + count(B) >= 2 && !(state("agent", "done") || event(A))'))).toEqual({
      signals: ['A', 'B'], states: [{ entity_id: 'agent', state_id: 'done' }],
    });
  });
});
