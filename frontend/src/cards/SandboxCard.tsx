import { CircleStop, Play, ShieldCheck } from "lucide-react";
import { useMemo, useState } from "react";
import { useWorldStore } from "../state/worldStore";
import type { WorldCard } from "../types/world";
import type { NodeSurfaceLevel } from "../state/nodeSurfaces";
import { InstrumentOutput, RelationshipList } from "./CardUtilities";

export function SandboxCardBody({ card }: { card: WorldCard; level: NodeSurfaceLevel }) {
  const edges = useWorldStore((state) => state.edges);
  const cards = useWorldStore((state) => state.cards);
  const startSandbox = useWorldStore((state) => state.startSandbox);
  const stopSandbox = useWorldStore((state) => state.stopSandbox);
  const executeSandbox = useWorldStore((state) => state.executeSandbox);
  const [command, setCommand] = useState("dir");
  const attached = useMemo(
    () => edges.filter((edge) => edge.target === card.id && edge.relationship.startsWith("mount_")),
    [card.id, edges],
  );
  const agents = useMemo(
    () => edges.filter((edge) => edge.target === card.id && edge.relationship === "execute"),
    [card.id, edges],
  );
  const output = Array.isArray(card.config.output) ? card.config.output.map(String) : [];
  const activeCommand = String(card.config.active_command ?? "");

  const ready = card.status === "ready" || card.status === "running";

  return (
    <div className="expanded-stack nodrag nopan">
      <div className="sandbox-status-panel">
        <div className="instrument-gauge" data-active={ready} aria-hidden="true"><i /><i /><i /></div>
        <div>
          <span>Execution environment</span>
          <strong>{ready ? "Contained and ready" : "Securely stopped"}</strong>
        </div>
        <button
          type="button"
          className={ready ? "secondary-button" : "primary-button"}
          onClick={() => void (ready ? stopSandbox(card.id) : startSandbox(card.id))}
        >
          {ready ? <CircleStop size={14} /> : <Play size={14} fill="currentColor" />}
          {ready ? "Stop" : "Start"}
        </button>
      </div>

      <form
        className="command-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (command.trim() && ready) void executeSandbox(card.id, command.trim());
        }}
      >
        <label htmlFor={`command-${card.id}`}>Command Prompt</label>
        <div>
          <span aria-hidden="true">C:\›</span>
          <input
            id={`command-${card.id}`}
            value={command}
            onChange={(event) => setCommand(event.target.value)}
            spellCheck={false}
            autoComplete="off"
            placeholder="echo hello world"
          />
          <button type="submit" disabled={!ready || !command.trim()} aria-label="Execute command">
            <Play size={13} fill="currentColor" />
          </button>
        </div>
      </form>

      <section className="card-section output-section sandbox-output-section">
        <div className="section-heading">
          <span>Terminal activity</span>
          <small>{activeCommand ? `running: ${activeCommand}` : "idle"}</small>
        </div>
        <InstrumentOutput lines={output} empty="Start the sandbox and run a contained command." />
      </section>

      <section className="card-section">
        <div className="section-heading"><span>Attached objects</span><small>{attached.length + agents.length} total</small></div>
        <RelationshipList card={card} empty="Connect an agent or resource to make it available here." />
      </section>

      <div className="security-strip">
        <ShieldCheck size={16} />
        <div><strong>Native Windows boundary</strong><span>{String(card.config.security ?? "AppContainer · Job Object · network denied")}</span></div>
      </div>
    </div>
  );
}
