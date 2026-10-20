// Dev-only fixtures: the production component and theme, without a runtime or model.
import { createRoot } from 'react-dom/client';
import { ExecutionGraphView } from '../src/cards/ExecutionGraph';
import { useLocale } from '../src/i18n';
import { graphFixture } from '../e2e/fixtures/executionGraph';
import type { ExecutionSourceHost } from '../src/plugins/executionSources';
import '../src/theme.css';
import '../src/workspace/workspacePrimitives.css';

const params = new URLSearchParams(location.search);
document.documentElement.dataset.theme = params.get('theme') ?? 'light';
useLocale.setState({ locale: params.get('locale') === 'zh-CN' ? 'zh-CN' : 'en' });
let snapshot = graphFixture(params.get('scenario') ?? 'parallel');
let invalidate: Parameters<ExecutionSourceHost['subscribe']>[0] | undefined;
const api: ExecutionSourceHost = {
  list: async () => ({ sources: [{ id: 'fixture', name: 'Research agent', type: 'agent' }] }),
  read: async (_source, page) => {
    const offset = Number(page?.cursor ?? 0), limit = page?.limit ?? 1000;
    const nodes = snapshot.nodes.slice(offset, offset + limit), ids = new Set(nodes.map(node => node.id));
    const last = offset + limit >= snapshot.nodes.length;
    const allIds = new Set(snapshot.nodes.map(node => node.id));
    return { ...snapshot, nodes, edges: snapshot.edges?.filter(edge => ids.has(edge.target) || (last && !allIds.has(edge.target))), next_cursor: last ? null : String(offset + limit) };
  },
  subscribe: listener => { invalidate = listener; return () => { invalidate = undefined; }; },
};
Object.assign(window, { updateGraphFixture: (next: typeof snapshot) => { snapshot = next; invalidate?.('execution'); } });
const width = Math.max(260, Number(params.get('width') ?? 500));
createRoot(document.getElementById('root')!).render(<main style={{ width, height: params.get('scenario') === 'independent' ? 300 : 740, maxWidth: '100vw', margin: '24px auto', background: 'var(--surface-solid)', borderRadius: 10, overflow: 'hidden', boxShadow: '0 1px 8px var(--shadow)' }}><ExecutionGraphView api={api} /></main>);
