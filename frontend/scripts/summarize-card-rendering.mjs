/** Compact, auditable summary. Pass the report.json files from profile-stress-zoom.mjs. */
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';

const args = process.argv.slice(2), destination = args.shift();
if (!destination || !args.length) throw new Error('Usage: node scripts/summarize-card-rendering.mjs output.json report.json ...');
const rows = [];
for (const file of args) {
  const report = JSON.parse(await readFile(file, 'utf8'));
  for (const run of report.results) {
    if (run.errors.length || run.syntheticRequests.length || run.initial.stress !== report.count || !run.initial.cards) throw new Error(`Invalid run: ${file}`);
    for (const [phase, measurement] of Object.entries(run.phases)) rows.push({
      label: report.label, scene: report.scene, zoom: report.zoom, phase, traced: !!measurement.traceMs,
      source: path.relative(path.dirname(destination), file).replaceAll('\\', '/'),
      initial: { mounted: run.initial.cards, onScreen: run.initial.onScreen, dom: run.initial.dom, heavy: run.initial.workspaces,
        lod: Object.values(run.initial.lod ?? {}).some(n => n > 0) ? run.initial.lod : null },
      p95: measurement.frameStats.p95, max: measurement.frameStats.max, elapsed: measurement.elapsed,
      views: [measurement.viewMounts, measurement.viewUnmounts], heavy: [measurement.heavyMounts, measurement.heavyUnmounts],
      wrappers: [measurement.mounts, measurement.unmounts], commits: measurement.commits.total ?? 0,
      dom: measurement.snapshot.dom, heavyInstances: measurement.snapshot.workspaces,
      styleMs: measurement.metrics.RecalcStyleDuration * 1000, layoutMs: measurement.metrics.LayoutDuration * 1000,
      traceMs: measurement.traceMs,
    });
  }
}
await writeFile(destination, JSON.stringify(rows, null, 2));
console.table(rows.map(r => ({ scene: r.scene, zoom: r.zoom, phase: r.phase, traced: r.traced, label: r.label,
  p95: r.p95?.toFixed(1), max: r.max?.toFixed(1), mounts: r.views.join('/'), commits: r.commits, dom: r.initial.dom, heavy: r.initial.heavy })));
