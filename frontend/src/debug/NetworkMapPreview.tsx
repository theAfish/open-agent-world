import { useState } from 'react';
import { NetworkMap } from '../graph/NetworkMap';
import type { NetworkData } from '../graph/types';

export function syntheticNetwork(count: number): NetworkData {
  const topics = ['Atomic structure', 'Simulation', 'Diffraction', 'Experiment', 'Literature', 'Analysis', 'Workflows', 'Discovery'];
  const kinds = ['capability', 'procedure', 'heuristic', 'limitation', 'memory'];
  const nodes = Array.from({ length: count }, (_, i) => ({ id: `node-${i}`, label: `${topics[i % 8]} ${Math.floor(i / 8) + 1}`,
    kind: kinds[Math.floor(i / 8) % kinds.length], topic: topics[i % 8] }));
  const edges = nodes.flatMap((n, i) => [8, 16, i % 17 === 0 ? 1 : 24].filter(offset => i >= offset)
    .map(offset => ({ id: `${i}:${offset}`, source: n.id, target: `node-${i - offset}`, label: offset === 1 ? 'informs' : 'related workflow' })));
  return { nodes, edges };
}

export function NetworkMapPreview() {
  const [data, setData] = useState(() => syntheticNetwork(240));
  const [key, setKey] = useState('240'), [mounted, setMounted] = useState(true);
  const [theme, setTheme] = useState('light'), [selected, setSelected] = useState('');
  return <main style={{ height: '100vh', boxSizing: 'border-box', padding: 28, display: 'flex', flexDirection: 'column', gap: 18, background: 'var(--surface-solid)', color: 'var(--ink)' }}>
    <header className="network-preview-toolbar" style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 16 }}>
      <div style={{ marginRight: 'auto' }}><strong style={{ fontSize: 20 }}>Knowledge atlas</strong><div style={{ fontSize: 12, opacity: .6 }}>Open Agent World · {data.nodes.length.toLocaleString()} entries · {data.edges.length.toLocaleString()} relationships</div></div>
      <select aria-label="Graph scale" value={key} onChange={e => { setKey(e.target.value); setData(syntheticNetwork(Number(e.target.value))); }}>
        {[240, 1000, 10000].map(n => <option key={n} value={String(n)}>{n.toLocaleString()} nodes</option>)}
        {key === 'snapshot' && <option value="snapshot">Real knowledge snapshot</option>}
      </select>
      <label>Open graph JSON <input aria-label="Open graph JSON" type="file" accept="application/json" style={{ width: 190 }} onChange={async e => {
        const file = e.target.files?.[0]; if (file) { const input = JSON.parse(await file.text()) as NetworkData;
          if (Array.isArray(input.nodes) && Array.isArray(input.edges)) { setKey('snapshot'); setData(input); } }
      }} /></label>
      <button onClick={() => { const next = theme === 'light' ? 'dark' : 'light'; setTheme(next); document.documentElement.dataset.theme = next; }}>Toggle theme</button>
      <button onClick={() => setMounted(!mounted)}>{mounted ? 'Unmount map' : 'Mount map'}</button>
    </header>
    <div style={{ flex: 1, minHeight: 0 }}>{mounted && <NetworkMap graphKey={`preview:${key}`} data={data} onSelect={setSelected} />}</div>
    <footer style={{ fontSize: 11, opacity: .65 }}>{selected || 'Drag to explore · Shift-drag to select · Scroll to zoom · Choose a topic to look closer'}</footer>
  </main>;
}
