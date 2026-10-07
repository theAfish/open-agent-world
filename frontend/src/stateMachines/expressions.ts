import type { MachineEntity, MachineEvent, StateMachine, TransitionRule } from './model';

export type TriggerExpression =
  | { op: 'number'; value: number }
  | { op: 'count' | 'event'; signal: string }
  | { op: 'state'; entity_id: string; state_id: string }
  | { op: 'add' | 'sub' | 'mul' | 'div' | 'mod' | 'eq' | 'ne' | 'gt' | 'gte' | 'lt' | 'lte'; left: TriggerExpression; right: TriggerExpression }
  | { op: 'all' | 'any'; args: TriggerExpression[] }
  | { op: 'not'; arg: TriggerExpression };

export interface TriggerSignal { id: string; label: string; match: MachineEvent }
export interface TriggerProgram {
  signals: TriggerSignal[];
  expression: TriggerExpression;
  window_seconds?: number | null;
  reset: 'on_match' | 'manual';
}
export interface ExpressionValidationContext {
  signals?: readonly Pick<TriggerSignal, 'id'>[];
  entities?: readonly Pick<MachineEntity, 'id' | 'states'>[];
}

export const EXPRESSION_LIMITS = { nodes: 128, depth: 12, signals: 16, source: 16_384, number: 1e12 } as const;
// Unlike $, this end assertion cannot match before a trailing line break.
const signalId = /^[A-Za-z][A-Za-z0-9_]{0,31}(?![\s\S])/;
const identifier = (value: unknown): value is string => typeof value === 'string' && value.length >= 1 && value.length <= 128 && !/[\s\x00]/.test(value);
const object = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const fields = (value: Record<string, unknown>, allowed: string[]) => Object.keys(value).every(key => allowed.includes(key));
const arithmetic = new Set(['add', 'sub', 'mul', 'div', 'mod']);
const comparisons = new Set(['eq', 'ne', 'gt', 'gte', 'lt', 'lte']);
type ValueType = 'number' | 'boolean';
type Issue = { message: string; node: unknown };

/** Inspect a bounded tree before traversing it anywhere else; no expression is executed. */
function inspectExpression(value: unknown, context: ExpressionValidationContext = {}, booleanRoot = true): Issue[] {
  const issues: Issue[] = [], active = new Set<object>();
  const signals = context.signals && new Set(context.signals.map(signal => signal.id));
  const entities = context.entities && new Map(context.entities.map(entity => [entity.id, entity]));
  let nodes = 0;
  const issue = (message: string, node: unknown) => { issues.push({ message, node }); return undefined; };
  const visit = (node: unknown, depth: number): ValueType | undefined => {
    if (++nodes > EXPRESSION_LIMITS.nodes) return issue(`An expression can contain at most ${EXPRESSION_LIMITS.nodes} nodes.`, node);
    if (depth > EXPRESSION_LIMITS.depth) return issue(`An expression can be at most ${EXPRESSION_LIMITS.depth} levels deep.`, node);
    if (!object(node) || typeof node.op !== 'string') return issue('Expected an expression node with an operator.', node);
    if (active.has(node)) return issue('Expressions cannot contain circular references.', node);
    active.add(node);
    try {
      if (node.op === 'number') {
        if (!fields(node, ['op', 'value']) || typeof node.value !== 'number' || !Number.isFinite(node.value) || Math.abs(node.value) > EXPRESSION_LIMITS.number)
          return issue('Numbers must be finite and between -1000000000000 and 1000000000000.', node);
        return 'number';
      }
      if (node.op === 'count' || node.op === 'event') {
        if (!fields(node, ['op', 'signal']) || typeof node.signal !== 'string' || !signalId.test(node.signal))
          return issue('Signal IDs must start with a letter and contain at most 32 letters, digits or underscores.', node);
        if (signals && !signals.has(node.signal)) issue(`Unknown signal "${node.signal}".`, node);
        return node.op === 'count' ? 'number' : 'boolean';
      }
      if (node.op === 'state') {
        if (!fields(node, ['op', 'entity_id', 'state_id']) || !identifier(node.entity_id) || !identifier(node.state_id))
          return issue('State checks need valid entity and state IDs.', node);
        if (entities && !entities.get(node.entity_id)?.states.some(state => state.id === node.state_id))
          issue(`State check refers to missing state "${node.entity_id}/${node.state_id}".`, node);
        return 'boolean';
      }
      if (arithmetic.has(node.op) || comparisons.has(node.op)) {
        if (!fields(node, ['op', 'left', 'right'])) return issue(`Unexpected fields for "${node.op}".`, node);
        const left = visit(node.left, depth + 1), right = visit(node.right, depth + 1);
        if (left && left !== 'number') issue(`Operator "${node.op}" requires a number on the left.`, node.left);
        if (right && right !== 'number') issue(`Operator "${node.op}" requires a number on the right.`, node.right);
        return arithmetic.has(node.op) ? 'number' : 'boolean';
      }
      if (node.op === 'all' || node.op === 'any') {
        if (!fields(node, ['op', 'args']) || !Array.isArray(node.args) || !node.args.length || node.args.length > 64)
          return issue(`Operator "${node.op}" requires 1–64 expressions.`, node);
        for (const arg of node.args) {
          const type = visit(arg, depth + 1);
          if (type && type !== 'boolean') issue(`Operator "${node.op}" requires boolean expressions.`, arg);
        }
        return 'boolean';
      }
      if (node.op === 'not') {
        if (!fields(node, ['op', 'arg'])) return issue('Unexpected fields for "not".', node);
        const type = visit(node.arg, depth + 1);
        if (type && type !== 'boolean') issue('Operator "not" requires a boolean expression.', node.arg);
        return 'boolean';
      }
      return issue(`Unknown expression operator "${node.op}".`, node);
    } finally { active.delete(node); }
  };
  const type = visit(value, 1);
  if (booleanRoot && type && type !== 'boolean') issue('The trigger expression must produce a boolean result; compare counts with a number.', value);
  return issues;
}

export function validateExpression(expression: TriggerExpression, context: ExpressionValidationContext = {}): string[] {
  return [...new Set(inspectExpression(expression, context).map(issue => issue.message))];
}

export function readExpression(value: unknown): TriggerExpression | null {
  if (inspectExpression(value, {}, false).length) return null;
  return JSON.parse(JSON.stringify(value)) as TriggerExpression;
}

export function createProgram(trigger: MachineEvent): TriggerProgram {
  return { signals: [{ id: 'A', label: 'A', match: { ...trigger } }], expression: { op: 'event', signal: 'A' }, reset: 'on_match' };
}

export function programSignals(rule: Pick<TransitionRule, 'program'>): TriggerSignal[] {
  return rule.program?.signals ?? [];
}

export function validateProgram(program: TriggerProgram, machine?: Pick<StateMachine, 'entities'>): string[] {
  const errors: string[] = [];
  if (!program.signals.length || program.signals.length > EXPRESSION_LIMITS.signals) errors.push('A trigger program needs 1–16 signals.');
  if (new Set(program.signals.map(signal => signal.id)).size !== program.signals.length) errors.push('Signal IDs must be unique within a rule.');
  if (program.reset !== 'on_match' && program.reset !== 'manual') errors.push('Choose whether counters reset after a match or manually.');
  if (program.window_seconds != null && (typeof program.window_seconds !== 'number' || !Number.isFinite(program.window_seconds)
    || program.window_seconds < 1 || program.window_seconds > 86400)) errors.push('The event window must be between 1 and 86400 seconds.');
  for (const signal of program.signals) {
    if (!signalId.test(signal.id)) errors.push('Signal IDs must start with a letter and contain at most 32 letters, digits or underscores.');
    if (!signal.label.trim() || signal.label.trim().length > 200) errors.push('Signal names must be 1–200 characters.');
    const match = signal.match;
    if (!identifier(match.entity_id) || (machine && !machine.entities.some(entity => entity.id === match.entity_id))) errors.push(`Source for signal "${signal.id}" does not exist.`);
    if (!identifier(match.event)) errors.push(`Event for signal "${signal.id}" must be 1–128 characters without whitespace.`);
    if (match.capability != null && !identifier(match.capability)) errors.push(`Capability for signal "${signal.id}" is invalid.`);
    if (match.target_card_id != null && !identifier(match.target_card_id)) errors.push(`Target card for signal "${signal.id}" is invalid.`);
  }
  errors.push(...validateExpression(program.expression, { signals: program.signals, entities: machine?.entities }));
  return [...new Set(errors)];
}

export function readProgram(value: unknown): TriggerProgram | null {
  if (!object(value) || !fields(value, ['signals', 'expression', 'window_seconds', 'reset']) || !Array.isArray(value.signals)
    || !value.signals.length || value.signals.length > EXPRESSION_LIMITS.signals) return null;
  const expression = readExpression(value.expression);
  if (!expression || (value.reset !== undefined && value.reset !== 'on_match' && value.reset !== 'manual')
    || (value.window_seconds != null && typeof value.window_seconds !== 'number')) return null;
  const signals: TriggerSignal[] = [];
  for (const signal of value.signals) {
    if (!object(signal) || !fields(signal, ['id', 'label', 'match']) || typeof signal.id !== 'string' || typeof signal.label !== 'string'
      || !object(signal.match) || !fields(signal.match, ['entity_id', 'event', 'capability', 'target_card_id', 'operation_id', 'state_id'])) return null;
    const match = signal.match;
    if (!identifier(match.entity_id) || !identifier(match.event)
      || (match.capability != null && !identifier(match.capability)) || (match.target_card_id != null && !identifier(match.target_card_id)) || (match.operation_id != null && !identifier(match.operation_id))) return null;
    signals.push({ id: signal.id, label: signal.label.trim(), match: { entity_id: match.entity_id, event: match.event,
      ...(match.capability == null ? {} : { capability: match.capability }), ...(match.target_card_id == null ? {} : { target_card_id: match.target_card_id }), ...(match.operation_id == null ? {} : { operation_id: match.operation_id }), ...(match.state_id == null ? {} : { state_id: String(match.state_id) }) } });
  }
  const program: TriggerProgram = { signals, expression,
    ...(value.window_seconds == null ? {} : { window_seconds: value.window_seconds }), reset: value.reset ?? 'on_match' };
  return validateProgram(program).length ? null : program;
}

export function expressionReferences(expression: TriggerExpression): { signals: string[]; states: { entity_id: string; state_id: string }[] } {
  const signals = new Set<string>(), states: { entity_id: string; state_id: string }[] = [];
  if (inspectExpression(expression, {}, false).length) return { signals: [], states };
  const visit = (node: TriggerExpression) => {
    if (node.op === 'count' || node.op === 'event') signals.add(node.signal);
    else if (node.op === 'state') states.push({ entity_id: node.entity_id, state_id: node.state_id });
    else if (node.op === 'all' || node.op === 'any') node.args.forEach(visit);
    else if (node.op === 'not') visit(node.arg);
    else if ('left' in node) { visit(node.left); visit(node.right); }
  };
  visit(expression);
  return { signals: [...signals], states };
}

export class ExpressionParseError extends Error {
  readonly line: number;
  readonly column: number;
  constructor(message: string, readonly position: number, source: string) {
    const lines = source.slice(0, position).split('\n');
    const line = lines.length, column = lines[lines.length - 1].length + 1;
    super(`${message} (line ${line}, column ${column})`);
    this.name = 'ExpressionParseError'; this.line = line; this.column = column;
  }
}

type Token = { kind: 'number' | 'identifier' | 'string' | 'symbol' | 'end'; text: string; value?: string | number; position: number };
function tokenize(source: string): Token[] {
  if (source.length > EXPRESSION_LIMITS.source) throw new ExpressionParseError('The expression is too long.', EXPRESSION_LIMITS.source, source);
  const tokens: Token[] = [];
  let offset = 0;
  while (offset < source.length) {
    if (/\s/.test(source[offset])) { offset++; continue; }
    const position = offset, rest = source.slice(offset);
    if (source[offset] === '"') {
      offset++;
      while (offset < source.length && source[offset] !== '"') { if (source[offset] === '\\') offset++; offset++; }
      if (offset >= source.length) throw new ExpressionParseError('Unterminated string.', position, source);
      const text = source.slice(position, ++offset);
      try { tokens.push({ kind: 'string', text, value: JSON.parse(text) as string, position }); }
      catch { throw new ExpressionParseError('Invalid quoted string.', position, source); }
    } else {
      const number = /^(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?/.exec(rest)?.[0];
      const name = /^[A-Za-z_][A-Za-z0-9_]*/.exec(rest)?.[0];
      const symbol = /^(?:&&|\|\||==|!=|>=|<=|[()+\-*/%!,<>])/.exec(rest)?.[0];
      if (number) { tokens.push({ kind: 'number', text: number, value: Number(number), position }); offset += number.length; }
      else if (name) { tokens.push({ kind: 'identifier', text: name, value: name, position }); offset += name.length; }
      else if (symbol) { tokens.push({ kind: 'symbol', text: symbol, position }); offset += symbol.length; }
      else throw new ExpressionParseError(`Unexpected character "${source[offset]}".`, offset, source);
    }
    if (tokens.length > 2048) throw new ExpressionParseError('The expression contains too many tokens.', position, source);
  }
  tokens.push({ kind: 'end', text: '', position: source.length });
  return tokens;
}

/** A small, typed DSL parser. References are checked separately against the current configuration. */
export function parseExpression(source: string): TriggerExpression {
  const tokens = tokenize(source), positions = new WeakMap<object, number>();
  let cursor = 0, created = 0, nesting = 0;
  const current = () => tokens[cursor];
  const fail = (message: string, token = current()): never => { throw new ExpressionParseError(message, token.position, source); };
  const consume = (text: string) => { if (current().text !== text) return false; cursor++; return true; };
  const expect = (text: string) => { if (!consume(text)) fail(`Expected "${text}".`); };
  const node = (value: TriggerExpression, position: number) => {
    if (++created > EXPRESSION_LIMITS.nodes) throw new ExpressionParseError(`An expression can contain at most ${EXPRESSION_LIMITS.nodes} nodes.`, position, source);
    positions.set(value, position); return value;
  };
  const nested = <T,>(parse: () => T): T => {
    if (++nesting > 64) fail('Too many nested parentheses or unary operators.');
    try { return parse(); } finally { nesting--; }
  };
  const argument = (quoted: boolean): string => {
    const token = current();
    if (token.kind !== 'string' && (quoted || token.kind !== 'identifier')) fail(quoted ? 'Expected a quoted ID.' : 'Expected a signal ID.');
    cursor++; return String(token.value);
  };
  const primary = (): TriggerExpression => {
    const token = current();
    if (consume('(')) { const result = nested(or); expect(')'); return result; }
    if (token.kind === 'number') { cursor++; return node({ op: 'number', value: Number(token.value) }, token.position); }
    if (token.kind !== 'identifier') return fail('Expected a number, count(...), event(...), state(...) or a parenthesized expression.');
    cursor++; expect('(');
    if (token.text === 'count' || token.text === 'event') {
      const signal = argument(false); expect(')'); return node({ op: token.text, signal }, token.position);
    }
    if (token.text === 'state') {
      const entity_id = argument(true); expect(','); const state_id = argument(true); expect(')');
      return node({ op: 'state', entity_id, state_id }, token.position);
    }
    // Explicit calls preserve single-argument all/any nodes when printing an AST.
    if (token.text === 'all' || token.text === 'any') {
      const args = [nested(or)]; while (consume(',')) args.push(nested(or)); expect(')');
      return node({ op: token.text, args }, token.position);
    }
    return fail(`Unknown function "${token.text}".`, token);
  };
  const unary = (): TriggerExpression => {
    const token = current();
    if (consume('!')) return node({ op: 'not', arg: nested(unary) }, token.position);
    if (consume('+')) return nested(unary);
    if (consume('-')) {
      const arg = nested(unary);
      if (arg.op === 'number') { arg.value = -arg.value; positions.set(arg, token.position); return arg; }
      return node({ op: 'sub', left: node({ op: 'number', value: 0 }, token.position), right: arg }, token.position);
    }
    return primary();
  };
  const product = (): TriggerExpression => {
    let left = unary();
    while (['*', '/', '%'].includes(current().text)) {
      const token = tokens[cursor++], right = unary();
      left = node({ op: token.text === '*' ? 'mul' : token.text === '/' ? 'div' : 'mod', left, right }, token.position);
    }
    return left;
  };
  const sum = (): TriggerExpression => {
    let left = product();
    while (['+', '-'].includes(current().text)) {
      const token = tokens[cursor++], right = product();
      left = node({ op: token.text === '+' ? 'add' : 'sub', left, right }, token.position);
    }
    return left;
  };
  const comparison = (): TriggerExpression => {
    const left = sum(), token = current();
    const ops: Record<string, 'eq' | 'ne' | 'gt' | 'gte' | 'lt' | 'lte'> = { '==': 'eq', '!=': 'ne', '>': 'gt', '>=': 'gte', '<': 'lt', '<=': 'lte' };
    if (!Object.hasOwn(ops, token.text)) return left;
    cursor++;
    return node({ op: ops[token.text], left, right: sum() }, token.position);
  };
  const and = (): TriggerExpression => {
    const first = comparison(), args = [first];
    while (consume('&&')) args.push(comparison());
    return args.length === 1 ? first : node({ op: 'all', args }, positions.get(first) ?? 0);
  };
  const or = (): TriggerExpression => {
    const first = and(), args = [first];
    while (consume('||')) args.push(and());
    return args.length === 1 ? first : node({ op: 'any', args }, positions.get(first) ?? 0);
  };
  const expression = or();
  if (current().kind !== 'end') fail(`Unexpected token "${current().text}".`);
  const issue = inspectExpression(expression)[0];
  if (issue) throw new ExpressionParseError(issue.message, object(issue.node) ? positions.get(issue.node) ?? 0 : 0, source);
  return expression;
}

export type ExpressionParseResult =
  | { expression: TriggerExpression; error?: never; position?: never; line?: never; column?: never }
  | { expression?: never; error: string; position: number; line: number; column: number };
export function tryParseExpression(source: string): ExpressionParseResult {
  try { return { expression: parseExpression(source) }; }
  catch (error) {
    if (!(error instanceof ExpressionParseError)) throw error;
    return { error: error.message, position: error.position, line: error.line, column: error.column };
  }
}

export function printExpression(expression: TriggerExpression): string {
  const issues = inspectExpression(expression, {}, false);
  if (issues.length) throw new Error(issues[0].message);
  const precedence = (node: TriggerExpression): number => node.op === 'any' ? 1 : node.op === 'all' ? 2
    : comparisons.has(node.op) ? 3 : node.op === 'add' || node.op === 'sub' ? 4
      : node.op === 'mul' || node.op === 'div' || node.op === 'mod' ? 5 : node.op === 'not' ? 6 : 7;
  const print = (node: TriggerExpression, parent = 0, equal = false): string => {
    let text: string;
    if (node.op === 'number') text = String(node.value);
    else if (node.op === 'count' || node.op === 'event') text = `${node.op}(${node.signal})`;
    else if (node.op === 'state') text = `state(${JSON.stringify(node.entity_id)}, ${JSON.stringify(node.state_id)})`;
    else if (node.op === 'not') text = `!${print(node.arg, 6)}`;
    else if (node.op === 'all' || node.op === 'any') {
      text = node.args.length === 1 ? `${node.op}(${print(node.args[0])})`
        : node.args.map(arg => print(arg, precedence(node), true)).join(node.op === 'all' ? ' && ' : ' || ');
    } else if ('left' in node) {
      const symbols = { add: '+', sub: '-', mul: '*', div: '/', mod: '%', eq: '==', ne: '!=', gt: '>', gte: '>=', lt: '<', lte: '<=' };
      text = `${print(node.left, precedence(node))} ${symbols[node.op]} ${print(node.right, precedence(node), true)}`;
    } else throw new Error('Unknown expression operator.');
    return precedence(node) < parent || (equal && precedence(node) === parent) ? `(${text})` : text;
  };
  return print(expression);
}
