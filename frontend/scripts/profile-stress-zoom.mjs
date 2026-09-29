/** Read-only browser projection; generates the same 1,000 ephemeral stress cards as DevelopmentPanel. */
import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const arg = (name, fallback) => process.argv.includes(name) ? process.argv[process.argv.indexOf(name) + 1] : fallback;
const origin = arg('--origin', 'http://127.0.0.1:5174');
const label = arg('--label', 'baseline');
const zoom = Number(arg('--zoom', '.18'));
const count = Number(arg('--count', '1000'));
const scene = arg('--scene', 'mixed');
const out = path.resolve(fileURLToPath(new URL('../../.outputs', import.meta.url)), `stress-zoom-${label}-${Date.now()}`);
await mkdir(out, { recursive: true }); console.log('OUTPUT', out);
const profile = await (await fetch(`${origin}/api/application`)).json();
const world = await (await fetch(`${origin}/api/world`)).json();
world.nodes=[]; world.edges=[];
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const browserCdp = await browser.newBrowserCDPSession();
const results = [];
const stat = values => { const a = values.slice().sort((x, y) => x - y); return { n: a.length, p50: a[Math.floor(a.length * .5)], p95: a[Math.floor(a.length * .95)], max: a.at(-1), over34: a.filter(x => x > 34).length }; };
try {
for (const variant of arg('--variants', 'normal').split(',')) {
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
  profile.values = { ...profile.values, 'oaw.locale': 'en', 'oaw-onboarding-v1': JSON.stringify({version:1,state:{status:'skipped'}}),
    'oaw-canvas-viewport-v1': JSON.stringify({version:0,state:{viewport:{x:960,y:540,zoom,width:1920,height:1080}}}), 'oaw-node-surfaces-v1': null };
  await context.route('**/api/**', async route => {
    const req = route.request(), u = new URL(req.url());
    if (req.method() !== 'GET') return route.fulfill({json:{}});
    if (u.pathname === '/api/application') return route.fulfill({json:profile});
    if (u.pathname === '/api/world') return route.fulfill({json:world});
    return route.continue();
  });
  await context.routeWebSocket('**/*', socket => socket.onMessage(() => {}));
  // Diagnostic substitutions exist only in this browser context, with unchanged
  // node data, positions and React Flow wrapper dimensions.
  if (variant === 'noTerrain') await context.route('**/src/canvas/TerrainBackground.tsx*', route => route.fulfill({
    contentType: 'application/javascript', body: 'export function TerrainBackground() { return null; }',
  }));
  if (variant === 'shell') await context.route('**/src/cards/CardFrame.tsx*', route => route.fulfill({
    contentType: 'application/javascript', body: `import React from '/node_modules/.vite/deps/react.js';
      export function CardContent() { return null; }
      export const WorldCardNode = React.memo(function DiagnosticCardShell({data}) {
        return React.createElement('div', {className:'diagnostic-card-shell', style:{width:'100%',height:'100%',background:'#dad6ce',border:'1px solid #aaa'}}, data.card.name);
      });`,
  }));
  if (variant === 'noSandbox') await context.route('**/src/cards/SandboxWorkspace.tsx*', route => route.fulfill({
    contentType: 'application/javascript', body: 'export function SandboxWorkspace() { return null; }',
  }));
  if (variant === 'noSettings') await context.route('**/src/cards/SandboxCard.tsx*', async route => {
    const response = await route.fetch();
    const original = await response.text();
    if (!original.includes('function SandboxSettings(')) throw new Error('Diagnostic settings substitution did not match');
    await route.fulfill({response, body: original + '\nSandboxSettings = function DiagnosticEmptySettings() { return null; };\n'});
  });
  if (variant === 'noCulling') await context.route('**/src/canvas/WorldCanvas.tsx*', async route => {
    const response = await route.fetch();
    const original = await response.text();
    const body = original.replace('onlyRenderVisibleElements: true', 'onlyRenderVisibleElements: false');
    if (body === original) throw new Error('Diagnostic culling substitution did not match');
    await route.fulfill({response, body});
  });
  await context.addInitScript(() => {
    window.__stress = { active: false, frames: [], wheels: [], tasks: [], commits: {}, samples: [], viewportWrites: 0 };
    window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = { supportsFiber:true,renderers:new Map(), inject(r){this.renderers.set(1,r);return 1;},
      onCommitFiberRoot(){if(window.__stress.active)window.__stress.commits.total=(window.__stress.commits.total??0)+1;},onCommitFiberUnmount(){},checkDCE(){} };
    new PerformanceObserver(list=>{if(window.__stress.active)window.__stress.tasks.push(...list.getEntries().map(e=>({start:e.startTime,duration:e.duration})));}).observe({type:'longtask'});
    document.addEventListener('wheel',e=>{if(window.__stress.active)window.__stress.wheels.push({at:performance.now(),age:performance.now()-e.timeStamp,delta:e.deltaY,x:e.clientX,y:e.clientY});},true);
    let previous;
    const tick=now=>{const m=window.__stress;if(m.active&&previous){m.frames.push(now-previous);const el=document.querySelector('#oaw-world-map .react-flow__viewport');if(el)m.samples.push({at:now,transform:el.style.transform});}previous=now;requestAnimationFrame(tick);};requestAnimationFrame(tick);
  });
  const page=await context.newPage(), errors=[], syntheticRequests=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',req=>{if(/\/api\/.*stress-\d+/.test(req.url()))syntheticRequests.push(req.url());});
  await page.goto(origin);
  if (variant !== 'noTerrain') await page.waitForFunction(()=>document.querySelector('.terrain-webgl-background')?.terrainStats?.pendingTiles===0);
  else await page.waitForSelector('#oaw-world-map');
  await page.evaluate(async ({count,scene})=>{const {useWorldStore}=await import(performance.getEntriesByType('resource').find(e=>/\/src\/state\/worldStore\.ts(?:\?|$)/.test(e.name))?.name ?? '/src/state/worldStore.ts');window.__world=useWorldStore;useWorldStore.getState().generateStressWorld(count);if(scene!=='mixed'){const {buildCardDraft}=await import('/src/state/helpers.ts');useWorldStore.setState(s=>({stressCards:s.stressCards.map(c=>({...buildCardDraft(scene,c.position),id:c.id,name:c.name,ephemeral:true}))}));}useWorldStore.subscribe((s,p)=>{if(window.__stress.active&&s.viewport!==p.viewport)window.__stress.viewportWrites++;});}, {count,scene});
  await page.waitForTimeout(3000);
  const point=await page.evaluate(()=>{for(let y=250;y<850;y+=35)for(let x=300;x<1600;x+=35)if(document.elementFromPoint(x,y)?.classList.contains('react-flow__pane'))return{x,y};throw Error('No empty canvas point');});
  const css={noCards:'.react-flow__node-worldCard { visibility:hidden !important; }',noFinish:'.card-finish-layer {display:none!important}',noLayers:'.can-cache-card {will-change:auto!important}',noShadow:'.world-card {box-shadow:none!important;backdrop-filter:none!important}',noHitTest:'.react-flow__node-worldCard, .react-flow__node-worldCard * {pointer-events:none!important}',cacheWorkspaces:'.react-flow__node-worldCard:has(.sandbox-workspace) {will-change:transform!important}'}[variant];
  if(css)await page.addStyleTag({content:css});
  await page.waitForTimeout(600);
  const snapshot=()=>page.evaluate(()=>({cards:document.querySelectorAll('.world-card').length,nodes:document.querySelectorAll('#oaw-world-map .react-flow__node').length,workspaces:document.querySelectorAll('.sandbox-workspace').length,dom:document.querySelectorAll('*').length,stress:window.__world.getState().stressCards.length,transform:document.querySelector('#oaw-world-map .react-flow__viewport')?.style.transform,terrain:document.querySelector('.terrain-webgl-background')?.terrainStats,cardDom: Object.fromEntries(['agent','text','image','sandbox'].map(type=>{const cards=Array.from(document.querySelectorAll(`.world-card--${type}`));return[type,{cards:cards.length,descendants:cards.reduce((n,c)=>n+c.querySelectorAll('*').length,0),hiddenSettings:cards.reduce((n,c)=>n+(c.querySelector('.sandbox-settings-window[hidden]')?.querySelectorAll('*').length??0),0)}];}))}));
  const initial=await snapshot();
  if (!initial.cards || !initial.nodes || initial.stress !== count || errors.length) throw new Error(`Invalid benchmark: ${JSON.stringify({initial,errors})}`);
  initial.onScreen = await page.evaluate(() => [...document.querySelectorAll('.world-card')].filter(el => {
    const r=el.getBoundingClientRect(); return r.right>=0 && r.bottom>=0 && r.left<=innerWidth && r.top<=innerHeight;
  }).length);
  initial.lod=await page.evaluate(()=>Object.fromEntries(['far','mid','full'].map(level=>[level,document.querySelectorAll(`[data-render-lod="${level}"]`).length])));
  await page.evaluate(()=>{
    const root=document.querySelector('#oaw-world-map .react-flow__nodes');
    const count=nodes=>Array.from(nodes).reduce((n,node)=>n+Number(node instanceof Element&&node.matches('.react-flow__node')),0);
    new MutationObserver(records=>{if(window.__stress.active)for(const r of records){window.__stress.mounts+=count(r.addedNodes);window.__stress.unmounts+=count(r.removedNodes);}}).observe(root,{childList:true});
    const collect=(nodes,selector,set)=>{for(const node of nodes)if(node instanceof Element){if(node.matches(selector))set.add(node);for(const child of node.querySelectorAll(selector))set.add(child);}};
    new MutationObserver(records=>{
      if(!window.__stress.active)return;
      const added=new Set(),removed=new Set(),heavyAdded=new Set(),heavyRemoved=new Set();
      for(const r of records){collect(r.addedNodes,'.world-card',added);collect(r.removedNodes,'.world-card',removed);collect(r.addedNodes,'.sandbox-workspace',heavyAdded);collect(r.removedNodes,'.sandbox-workspace',heavyRemoved);}
      window.__stress.viewMounts+=added.size;window.__stress.viewUnmounts+=removed.size;window.__stress.heavyMounts+=heavyAdded.size;window.__stress.heavyUnmounts+=heavyRemoved.size;
    }).observe(root,{childList:true,subtree:true});
  });
  const cdp=await context.newCDPSession(page);await cdp.send('Performance.enable');await cdp.send('Emulation.setCPUThrottlingRate',{rate:Number(arg('--cpu','1'))});
  const events=[];
  if(process.argv.includes('--trace')){cdp.on('Tracing.dataCollected',e=>events.push(...e.value));await cdp.send('Tracing.start',{categories:'devtools.timeline,disabled-by-default-devtools.timeline,disabled-by-default-v8.cpu_profiler,blink,blink.user_timing,cc,gpu',transferMode:'ReportEvents'});}
  if(process.argv.includes('--profile')){await cdp.send('Profiler.enable');await cdp.send('Profiler.start');}
  const phases={};
  for(const phase of arg('--phases','pan,zoom,reverse').split(',')){
    await page.evaluate(phase=>{performance.mark(`stress-${phase}-start`);Object.assign(window.__stress,{active:true,frames:[],wheels:[],tasks:[],commits:{},samples:[],viewportWrites:0,mounts:0,unmounts:0,viewMounts:0,viewUnmounts:0,heavyMounts:0,heavyUnmounts:0});},phase);
    const before=await cdp.send('Performance.getMetrics'),procBefore=await browserCdp.send('SystemInfo.getProcessInfo'),start=Date.now();
    if(phase==='pan'){
      for(let pass=0;pass<2;pass++){await page.mouse.move(960,540);await page.mouse.down({button:'middle'});for(let i=1;i<=60;i++){await page.mouse.move(960+300*Math.sin(i/60*Math.PI*2),540+80*Math.sin(i/60*Math.PI*4));await page.waitForTimeout(12);}await page.mouse.up({button:'middle'});}
    }else if(phase==='rapidPan'){
      // Continuous pan and reversal on a fixed cadence, independent of renderer acknowledgements.
      await cdp.send('Input.dispatchMouseEvent',{type:'mousePressed',x:960,y:540,button:'middle',buttons:4,clickCount:1});
      const commands=[];
      for(let i=1;i<=120;i++){commands.push(cdp.send('Input.dispatchMouseEvent',{type:'mouseMoved',x:960+300*Math.sin(i/60*Math.PI*2),y:540+80*Math.sin(i/60*Math.PI*4),button:'middle',buttons:4}));await new Promise(r=>setTimeout(r,8));}
      await Promise.all(commands);
      await cdp.send('Input.dispatchMouseEvent',{type:'mouseReleased',x:960,y:540,button:'middle',buttons:0,clickCount:1});
    }else if(phase==='zoom'){
      await page.mouse.move(point.x,point.y);
      for(const sign of [-1,1])for(let i=0;i<20;i++){await page.mouse.wheel(0,sign*24);await page.waitForTimeout(16);}
    }else{
      // Send on a wall-clock cadence without awaiting acknowledgements from a blocked renderer.
      const commands=[];
      for(let i=0;i<40;i++){commands.push(cdp.send('Input.dispatchMouseEvent',{type:'mouseWheel',x:point.x,y:point.y,deltaX:0,deltaY:i<20?-24:24}));await new Promise(r=>setTimeout(r,8));}
      await Promise.all(commands);
    }
    await page.waitForTimeout(450);
    const elapsed=Date.now()-start,after=await cdp.send('Performance.getMetrics'),procAfter=await browserCdp.send('SystemInfo.getProcessInfo');
    const measured=await page.evaluate(phase=>{window.__stress.active=false;performance.mark(`stress-${phase}-end`);return window.__stress;},phase);
    const processes=procAfter.processInfo.map(p=>({...p,cpu:p.cpuTime-(procBefore.processInfo.find(b=>b.id===p.id)?.cpuTime??p.cpuTime)}));
    phases[phase]={elapsed,...measured,snapshot:await snapshot(),frameStats:stat(measured.frames),inputAge:stat(measured.wheels.map(e=>e.age)),processes,
      metrics:Object.fromEntries(after.metrics.filter(m=>/Duration|LayoutCount|RecalcStyleCount/.test(m.name)).map(m=>[m.name,m.value-(before.metrics.find(b=>b.name===m.name)?.value??0)]))};
    console.log(variant,phase,JSON.stringify({elapsed,frames:phases[phase].frameStats,inputAge:phases[phase].inputAge,metrics:phases[phase].metrics,commits:measured.commits,viewportWrites:measured.viewportWrites}));
  }
  if(process.argv.includes('--profile')){const {profile}=await cdp.send('Profiler.stop');await writeFile(path.join(out,`cpu-${variant}.json`),JSON.stringify(profile));}
  if(process.argv.includes('--trace')){const done=new Promise(resolve=>cdp.once('Tracing.tracingComplete',resolve));await cdp.send('Tracing.end');await done;await writeFile(path.join(out,`trace-${variant}.json`),JSON.stringify({traceEvents:events}));}
  await page.screenshot({path:path.join(out,`${variant}.png`)});
  results.push({variant,initial,phases,errors,syntheticRequests});
  if(events.length){const main=events.find(e=>e.name==='thread_name'&&e.args.name==='CrRendererMain');for(const [phase,value] of Object.entries(phases)){const start=events.find(e=>e.name===`stress-${phase}-start`)?.ts,end=events.find(e=>e.name===`stress-${phase}-end`)?.ts;const totals={};for(const e of events)if(e.ph==='X'&&e.dur&&e.pid===main.pid&&e.tid===main.tid&&e.ts>=start&&e.ts<end&&/^(UpdateLayoutTree|Layout|Blink.Paint.UpdateTime|LayoutView::HitTest|Layerize)$/.test(e.name))totals[e.name]=(totals[e.name]??0)+e.dur/1000;value.traceMs=totals;}}
  await writeFile(path.join(out,'report.json'),JSON.stringify({label,origin,zoom,count,scene,cpu:Number(arg('--cpu','1')),readonly:true,results},null,2));
  await context.close();
}
}finally{await browser.close();}
