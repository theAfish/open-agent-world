import { parse_structure_file } from 'matterviz/structure/parse';
self.onmessage = (event: MessageEvent<{ id: number; name: string; data: string }>) => {
  const { id, name, data } = event.data;
  try {
    const start = performance.now();
    const bytes = Uint8Array.from(atob(data), c => c.charCodeAt(0));
    const structure = parse_structure_file(new TextDecoder('utf-8', { fatal: true }).decode(bytes), name);
    if (!structure?.sites?.length) throw new Error('No atoms found in this structure file.');
    if (structure.sites.length > 20000) throw new Error('This viewer supports up to 20,000 atoms per structure.');
    self.postMessage({ id, structure, milliseconds: performance.now() - start });
  } catch (error) { self.postMessage({ id, error: String(error) }); }
};
