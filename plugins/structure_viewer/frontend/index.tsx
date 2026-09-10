import { useEffect, useRef, useState } from "react";
import { useFileViewer, type FrontendPlugin, type PluginViewProps } from "@oaw/plugin-api";
import "./style.css";

const supported = (name: string) => /(?:\.(?:cif|mcif|poscar|vasp|xyz|extxyz|json)|(?:^|\/)(?:POSCAR|CONTCAR))$/i.test(name);

function Preview({ card }: PluginViewProps) {
  const { file, sources } = useFileViewer(card.id);
  return <p className="structure-summary">{file?.name ?? (sources.length ? "Open a structure file in a connected window" : "Connect a Sandbox or Conversation")}</p>;
}

function Viewer({ card, host, level }: PluginViewProps) {
  const { file, sources, pinned, setPinned } = useFileViewer(card.id);
  const target = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const source = sources.find(item => item.id === file?.reference.source_id);
  const active = level === "inspector" || level === "workspace";
  useEffect(() => {
    const controller = new AbortController();
    let dispose: (() => void) | undefined;
    setError(""); setStatus("");
    if (!active || !file || !target.current) return;
    if (!supported(file.name)) { setError("This file is not a supported structure. Open CIF, POSCAR, XYZ, or structure JSON."); return; }
    const container = target.current;
    setStatus("Loading structure…");
    void (async () => {
      const content = await host.readFile(file.reference, controller.signal);
      if (controller.signal.aborted) return;
      const { renderStructure } = await import("./render");
      if (controller.signal.aborted) return;
      dispose = renderStructure(container, content, message => { if (!controller.signal.aborted) setError(message); });
      setStatus("");
    })().catch(error => {
      if (!controller.signal.aborted) { setError(error instanceof Error ? error.message : String(error)); setStatus(""); }
    });
    return () => { controller.abort(); dispose?.(); };
  }, [file, host, reload, active]);
  if (!active) return <p className="structure-summary">{file?.name ?? "Connect a file source"}</p>;
  return <section className={`structure-viewer structure-viewer--${level}`} aria-label="Structure viewer">
    <div className="structure-toolbar">
      <div><strong title={file?.name}>{file?.name ?? "Structure viewer"}</strong><small>{source?.name ?? "CIF · POSCAR · XYZ · JSON"}</small></div>
      <button type="button" disabled={!file} aria-pressed={pinned} onClick={() => setPinned(!pinned)}>{pinned ? "Pinned" : "Following"}</button>
      <button type="button" disabled={!file} onClick={() => setReload(value => value + 1)}>Reload</button>
    </div>
    <div className="structure-stage nodrag nopan nowheel">
      <div ref={target} className="structure-canvas" />
      {!file && <div className="structure-empty">{sources.length ? "Open a structure file in a connected window" : "Connect this card to a Sandbox or Conversation"}</div>}
      {status && <div className="structure-empty" role="status">{status}</div>}
      {error && <div className="structure-empty" role="alert">{error}</div>}
    </div>
  </section>;
}

export default { apiVersion: 1, views: { preview: Preview, viewer: Viewer } } satisfies FrontendPlugin;
