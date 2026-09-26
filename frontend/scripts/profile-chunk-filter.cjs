// Actual-source microbenchmark, independent of browser/store initialization.
// node scripts/profile-chunk-filter.cjs [path-to-baseline-frontend]
const fs = require('node:fs');
const path = require('node:path');
const { performance } = require('node:perf_hooks');
const ts = require('typescript');
const frontend = path.resolve(__dirname, '..');
function declarations(root, file, names) {
  const source = fs.readFileSync(path.join(root, 'src/state', file), 'utf8');
  const ast = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true);
  return ast.statements.filter(node => names.includes(node.name?.text)
    || (ts.isVariableStatement(node) && node.declarationList.declarations.some(item => names.includes(item.name?.text))))
    .map(node => node.getText(ast).replace(/^export /, '')).join('\n');
}
function implementation(root) {
  const source = [
    declarations(root, 'cardIndex.ts', ['indexes', 'cardIndex']),
    declarations(root, 'containers.ts', ['containerDefinition', 'isContainer', 'descendants', 'ancestors', 'parentFirst']),
    declarations(root, 'chunks.ts', ['CHUNK_SIZE', 'chunkKey', 'positionToChunk', 'filterCardsToChunks']),
  ].join('\n');
  const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;
  return new Function(compiled + '\nreturn filterCardsToChunks;')();
}
const current = implementation(frontend);
const before = process.argv[2] ? implementation(path.resolve(process.argv[2])) : null;
const catalog = { node_types: [{ id: 'legion', container: {} }, { id: 'text' }] };
const keys = new Set(['0:0', '1:0', '0:1', '1:1']);
function card(id, parent_id, type = 'text') {
  return { id, parent_id, type, name: id, config: {}, status: 'available', expanded: false,
    position: { x: 3000 + Number(id.replace(/\D/g, '') || 0) * 12, y: 100 }, size: { width: 180, height: 100 } };
}
function scene(n, topology) {
  if (topology === 'flat') return Array.from({ length: n }, (_, i) => card(`c${i}`));
  const groups = topology === 'single-legion' ? 1 : topology === 'nested-depth10' ? 10 : 13;
  const nodes = Array.from({ length: groups }, (_, i) => ({ ...card(`g${i}`, i ? topology === 'nested-depth10' ? `g${i - 1}` : i <= 3 ? 'g0' : `g${1 + Math.floor((i - 4) / 3)}` : undefined, 'legion'),
    position: { x: 0, y: 0 }, size: { width: 50000, height: 2000 } }));
  for (let i = groups; i < n; i++) nodes.push(card(`c${i}`, groups === 13 ? `g${4 + i % 9}` : `g${groups - 1}`));
  return nodes;
}
let sink = 0;
function profile(fn) {
  for (let i = 0; i < 100; i++) sink += fn().length;
  const values = [];
  for (let i = 0; i < 300; i++) { const start = performance.now(); sink += fn().length; values.push(performance.now() - start); }
  values.sort((a, b) => a - b);
  return { median_ms: values[150], p95_ms: values[285] };
}
const results = [];
for (const n of [250, 1000]) for (const topology of ['flat', 'single-legion', 'nested-3-level', 'nested-depth10']) {
  const cards = scene(n, topology);
  if (before && JSON.stringify(current(cards, keys, catalog).map(c => c.id)) !== JSON.stringify(before(cards, keys, catalog).map(c => c.id))) {
    throw Error(`Ordered selection differs: ${n} ${topology}`);
  }
  results.push({ cards: n, topology, before: before && profile(() => before(cards, keys, catalog)), after: profile(() => current(cards, keys, catalog)) });
}
console.log(JSON.stringify({ node: process.version, platform: process.platform, warmups: 100, samples: 300, results, sink }, null, 2));
