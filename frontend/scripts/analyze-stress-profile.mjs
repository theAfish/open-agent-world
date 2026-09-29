/** Summarize a diagnostic trace; nested durations are inclusive, never additive. */
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
const dir = path.resolve(process.argv[2]);
const variant = process.argv[3] ?? 'normal';
const events = JSON.parse(await readFile(path.join(dir, `trace-${variant}.json`), 'utf8')).traceEvents;
const profile = JSON.parse(await readFile(path.join(dir, `cpu-${variant}.json`), 'utf8'));
const main = events.find(e => e.name === 'thread_name' && e.args.name === 'CrRendererMain');
const durations = new Map();
for (const e of events) {
  if (e.ph !== 'X' || !e.dur || e.pid !== main.pid || e.tid !== main.tid) continue;
  const v = durations.get(e.name) ?? { name:e.name, count:0, ms:0, max:0 };
  v.count++; v.ms += e.dur / 1000; v.max = Math.max(v.max, e.dur / 1000); durations.set(e.name,v);
}
const nodes = new Map(profile.nodes.map(n => [n.id,n]));
const parents = new Map();
for (const n of nodes.values()) for (const child of n.children ?? []) parents.set(child,n.id);
const self = new Map(), inclusive = new Map();
const key = n => `${n.callFrame.functionName || '(anonymous)'} ${n.callFrame.url}:${n.callFrame.lineNumber+1}`;
for (let i=0;i<profile.samples.length;i++) {
  let id=profile.samples[i]; const ms=profile.timeDeltas[i]/1000;
  const name=key(nodes.get(id)); self.set(name,(self.get(name)??0)+ms);
  const seen=new Set();
  while(id) { const name=key(nodes.get(id)); if(!seen.has(name)){inclusive.set(name,(inclusive.get(name)??0)+ms);seen.add(name);} id=parents.get(id); }
}
const ranked = m => [...m].map(([name,ms])=>({name,ms:Math.round(ms*10)/10})).sort((a,b)=>b.ms-a.ms);
const phases={};
for(const phase of ['pan','zoom','reverse']) {
  const start=events.find(e=>e.name===`stress-${phase}-start`)?.ts;
  const end=events.find(e=>e.name===`stress-${phase}-end`)?.ts;
  if(!start||!end)continue;
  const totals=new Map();
  for(const e of events)if(e.ph==='X'&&e.dur&&e.pid===main.pid&&e.tid===main.tid&&e.ts>=start&&e.ts<end)totals.set(e.name,(totals.get(e.name)??0)+e.dur/1000);
  const source=new Map();let time=profile.startTime;
  for(let i=0;i<profile.samples.length;i++) {
    const ms=profile.timeDeltas[i]/1000;time+=profile.timeDeltas[i];
    if(time<start||time>=end)continue;
    let id=profile.samples[i];const seen=new Set();
    while(id){const name=key(nodes.get(id));if(name.includes('/src/')&&!seen.has(name)){source.set(name,(source.get(name)??0)+ms);seen.add(name);}id=parents.get(id);}
  }
  phases[phase]={ms:(end-start)/1000,duration:ranked(totals),sourceInclusive:ranked(source).slice(0,20)};
}
const summary={main, phases, duration:[...durations.values()].sort((a,b)=>b.ms-a.ms),self:ranked(self),inclusive:ranked(inclusive)};
await writeFile(path.join(dir,`analysis-${variant}.json`),JSON.stringify(summary,null,2));
console.log(JSON.stringify({duration:summary.duration.slice(0,30),self:summary.self.slice(0,25),sourceInclusive:summary.inclusive.filter(n=>n.name.includes('/src/')).slice(0,30)},null,2));
