/** Offline HTTP fixture for the host Store acceptance; no GitHub dependency. */
import { createServer } from 'node:http';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';

export async function startFakeMarketplace(artifact, python) {
  const data = await readFile(artifact);
  const manifest = JSON.parse(execFileSync(python, ['-c',
    'import sys,zipfile; print(zipfile.ZipFile(sys.argv[1]).read("manifest.json").decode())', artifact], { encoding: 'utf8', windowsHide: true }));
  const metadata = { pack_id: manifest.id, version: manifest.version, sha256: createHash('sha256').update(data).digest('hex'), size_bytes: data.length, manifest };
  const listing = { id: manifest.id, name: manifest.name, summary: 'External Greeter Pack', description: 'A greeting card for your world.', latest_version: manifest.version };
  const items = [listing, ...Array.from({ length: 22 }, (_, i) => ({ ...listing, id: `example.sample${String(i).padStart(2, '0')}`, name: `Sample ${i}`, summary: 'Pagination fixture' }))];
  const requests = [];
  const server = createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost'); requests.push(url.pathname + url.search);
    const json = value => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(value)); };
    const base = `/v1/packs/${manifest.id}`;
    if (url.pathname === '/v1/packs') {
      const query = (url.searchParams.get('query') ?? '').toLowerCase();
      const cursor = url.searchParams.get('cursor') ?? '';
      const limit = Math.min(Number(url.searchParams.get('limit') ?? 20), 100);
      const matches = items.filter(item => item.id > cursor && `${item.id} ${item.name} ${item.summary}`.toLowerCase().includes(query));
      const page = matches.slice(0, limit);
      return json({ items: page, next_cursor: matches.length > limit ? page.at(-1).id : null });
    }
    if (url.pathname === base) return json({ ...listing, versions: [manifest.version], versions_next_cursor: null });
    if (url.pathname === `${base}/versions/${manifest.version}`) return json(metadata);
    if (url.pathname === `${base}/versions`) return json({ items: [metadata], next_cursor: null });
    if (url.pathname === `${base}/versions/${manifest.version}/download`) {
      res.writeHead(200, { 'Content-Type': 'application/vnd.oaw.pack', 'Content-Length': data.length, 'X-OAW-Pack-SHA256': metadata.sha256 });
      res.write(data.subarray(0, 20)); return res.end(data.subarray(20));
    }
    res.statusCode = 404; json({ error: { message: 'Pack unavailable' } });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return { url: `http://127.0.0.1:${server.address().port}`, metadata, requests,
    stop: () => new Promise((resolve, reject) => { server.close(error => error ? reject(error) : resolve()); server.closeAllConnections(); }) };
}
