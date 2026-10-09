import { useEffect, useMemo, useRef, useState } from 'react';
import { envelope, format, type Kind, type Plot } from './plot';

const colors = ['#438ec0', '#d78c51', '#52a797', '#a27ac3', '#d16c84', '#a4a149'];
type Hit = {x: number; y: number; text: string};

export function ChartCanvas({plot, kind, xLabel, yLabel}: {plot: Plot; kind: Exclude<Kind, 'graph'>; xLabel: string; yLabel: string}) {
  const canvas = useRef<HTMLCanvasElement>(null), frame = useRef<HTMLDivElement>(null), hits = useRef<Hit[]>([]);
  const [size, setSize] = useState({width: 600, height: 320}), [tip, setTip] = useState<Hit | null>(null);
  const [view, setView] = useState({zoom: 1, x: 0, y: 0});
  const [theme, setTheme] = useState(0);
  const drag = useRef<{x: number; y: number; left: number; top: number} | null>(null);
  const groups = useMemo(() => [...new Set(plot.points.map(p => p.series))], [plot, kind]);
  useEffect(() => { setView({zoom: 1, x: 0, y: 0}); setTip(null); }, [plot]);
  useEffect(() => {
    const observer = new MutationObserver(() => setTheme(value => value + 1));
    observer.observe(document.documentElement, {attributes:true, attributeFilter:['data-theme','class','style']});
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!frame.current) return;
    const observer = new ResizeObserver(entries => { const {width, height} = entries[0].contentRect; setSize({width, height}); });
    observer.observe(frame.current); return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const element = canvas.current, ctx = element?.getContext('2d');
    if (!element || !ctx || size.width < 1 || size.height < 1) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2), {width: w, height: h} = size;
    element.width = w * dpr; element.height = h * dpr;
    ctx.scale(dpr, dpr); ctx.clearRect(0, 0, w, h);
    const text = getComputedStyle(element).color;
    ctx.font = '11px system-ui'; hits.current = [];
    const left = 62, top = 16, right = w - 20, bottom = h - 48;
    const width = Math.max(1, right - left), height = Math.max(1, bottom - top);
    const groupIndices = new Map(groups.map((group,index)=>[group,index]));
    const color = (group: string) => colors[(groupIndices.get(group) ?? 0) % colors.length];
    if (!plot.points.length) return;
    const xs = plot.points.map(p => p.x), ys = plot.points.map(p => p.y);
    let xmin = Math.min(...xs), xmax = Math.max(...xs), ymin = Math.min(...ys), ymax = Math.max(...ys);
    if (kind === 'bar' || kind === 'histogram') { ymin = Math.min(0, ymin); ymax = Math.max(0, ymax); xmin -= .5 * (kind === 'bar' ? 1 : (xmax - xmin) / Math.max(1, xs.length - 1)); xmax += .5 * (kind === 'bar' ? 1 : (xmax - xmin) / Math.max(1, xs.length - 1)); }
    if (xmin === xmax) {xmin -= .5; xmax += .5;} if (ymin === ymax) {ymin -= .5; ymax += .5;}
    const pad = (ymax - ymin) * .06; ymin -= pad; ymax += pad;
    const px = (x: number) => left + (x - xmin) / (xmax - xmin) * width * view.zoom + view.x;
    const py = (y: number) => bottom - (y - ymin) / (ymax - ymin) * height;
    ctx.textAlign = 'right'; ctx.fillStyle = text;
    for (let i = 0; i <= 4; i++) {
      const v = ymin + (ymax - ymin) * i / 4, y = py(v);
      ctx.globalAlpha = .12; ctx.strokeStyle = text; ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
      ctx.globalAlpha = .8; ctx.fillText(format(v), left - 10, y + 4);
    }
    ctx.textAlign = 'center';
    const ticks = Math.max(2, Math.floor(width / 110));
    for (let i = 0; i <= ticks; i++) {
      const value = xmin + ((i / ticks * width - view.x) / view.zoom) / width * (xmax - xmin);
      const label = plot.categories.length ? plot.categories[Math.round(value)] ?? '' : plot.dateX ? new Date(value).toISOString().slice(0, 10) : format(value);
      ctx.fillText(label.slice(0, 18), left + i / ticks * width, bottom + 19);
    }
    ctx.globalAlpha = 1; ctx.fillText(xLabel, left + width / 2, h - 5);
    ctx.save(); ctx.translate(12, top + height / 2); ctx.rotate(-Math.PI / 2); ctx.fillText(yLabel, 0, 0); ctx.restore();
    ctx.save(); ctx.beginPath(); ctx.rect(left, top, width, height); ctx.clip();
    const grouped = new Map<string, Plot['points']>();
    for (const point of plot.points) { const group = grouped.get(point.series); if (group) group.push(point); else grouped.set(point.series, [point]); }
    for (const [group, points] of grouped) {
      ctx.fillStyle = color(group); ctx.strokeStyle = color(group); ctx.lineWidth = 2;
      if (kind === 'line') {
        ctx.beginPath(); envelope([...points].sort((a, b) => a.x - b.x), Math.ceil(width * view.zoom)).forEach((p, i) => { if (i === 0) ctx.moveTo(px(p.x), py(p.y)); else ctx.lineTo(px(p.x), py(p.y)); }); ctx.stroke();
      }
      for (const p of points) {
        let x = px(p.x), y = py(p.y);
        if (kind === 'bar' || kind === 'histogram') {
          const band = width * view.zoom / Math.max(1, kind === 'bar' ? plot.categories.length : points.length);
          const barWidth = Math.max(.6, band * .78 / groups.length);
          x += ((groupIndices.get(group) ?? 0) - (groups.length - 1) / 2) * barWidth;
          ctx.fillRect(x - barWidth / 2, Math.min(y, py(0)), barWidth, Math.max(1, Math.abs(py(0) - y)));
        } else if (kind === 'scatter' || points.length <= 80) {ctx.beginPath(); ctx.arc(x, y, kind === 'scatter' ? 2.7 : 3, 0, 2 * Math.PI); ctx.fill();}
        if (x >= left && x <= right) hits.current.push({x, y, text: `${p.label}: ${format(p.y)}${group ? ` · ${group}` : ''}`});
      }
    }
    ctx.restore();
  }, [plot, kind, size, view, groups, xLabel, yLabel, theme]);
  return <div className="viz-canvas" ref={frame} tabIndex={0} onKeyDown={e=>{
    if(!['+','=','-','Home','ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key))return;
    e.preventDefault(); e.stopPropagation();
    setView(v=>e.key==='Home'?{zoom:1,x:0,y:0}:{zoom:e.key==='+'||e.key==='='?Math.min(20,v.zoom*1.2):e.key==='-'?Math.max(1,v.zoom/1.2):v.zoom,x:v.x+(e.key==='ArrowLeft'?-25:e.key==='ArrowRight'?25:0),y:v.y+(e.key==='ArrowUp'?-25:e.key==='ArrowDown'?25:0)});
  }} onDoubleClick={e=>{e.stopPropagation();setView({zoom:1,x:0,y:0});}}
    onWheel={e => {e.stopPropagation(); setTip(null); const factor = e.deltaY < 0 ? 1.2 : 1 / 1.2; setView(v => ({...v, zoom: Math.max(1, Math.min(20, v.zoom * factor))}));}}
    onPointerDown={e => {e.stopPropagation(); e.currentTarget.setPointerCapture(e.pointerId); drag.current = {x:e.clientX,y:e.clientY,left:view.x,top:view.y};}}
    onPointerUp={e => {drag.current = null; if(e.currentTarget.hasPointerCapture(e.pointerId))e.currentTarget.releasePointerCapture(e.pointerId);}}
    onPointerCancel={() => {drag.current = null;}}
    onPointerLeave={() => setTip(null)}
    onPointerMove={e => {
      const rect = e.currentTarget.getBoundingClientRect(), sx = size.width / rect.width, sy = size.height / rect.height;
      if (drag.current) { const d = drag.current; setView(v => ({...v, x:d.left+(e.clientX-d.x)*sx, y:d.top+(e.clientY-d.y)*sy})); return; }
      const x = (e.clientX - rect.left)*sx, y = (e.clientY - rect.top)*sy;
      let nearest: Hit | null = null, distance = 25;
      for (const p of hits.current) { const d = Math.hypot(p.x-x,p.y-y); if(d<distance){nearest=p;distance=d;} } setTip(nearest);
    }}>
    <canvas ref={canvas} role="img" aria-label={`${kind} chart: ${plot.points.length} points`} />
    {tip && <div className="viz-tooltip" style={{left: Math.min(tip.x + 12, size.width - 190), top: Math.max(4, tip.y - 32)}}>{tip.text}</div>}
    {view.zoom !== 1 || view.x !== 0 || view.y !== 0 ? <button className="viz-reset" onPointerDown={e=>e.stopPropagation()} onClick={()=>setView({zoom:1,x:0,y:0})}>↺ 1:1</button> : null}
    {groups.some(Boolean) && <div className="viz-legend">{groups.slice(0, 8).map((group,i)=><span key={group}><i style={{background:colors[i%colors.length]}}/>{group}</span>)}{groups.length > 8 && <span>+{groups.length-8}</span>}</div>}
  </div>;
}
