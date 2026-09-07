import { worldApi, apiErrorMessage } from "../api/client";
import { CircleStop, Play, Radio } from "lucide-react";
import { useEffect, useState } from "react";
import { useNodeSurfaceStore } from "../state/nodeSurfaces";
import { useWorldStore } from "../state/worldStore";
import type { WorldCard } from "../types/world";
import type { NodeSurfaceLevel } from "../state/nodeSurfaces";
import { InstrumentOutput } from "./CardUtilities";

export function AgentCardBody({ card, level }: { card: WorldCard; level: NodeSurfaceLevel }) {
  const edges = useWorldStore((state) => state.edges);
  const cards = useWorldStore((state) => state.cards);
  const updateCard = useWorldStore((state) => state.updateCard);
  const runAgent = useWorldStore((state) => state.runAgent);
  const stopAgent = useWorldStore((state) => state.stopAgent);
  const modelSettings = useWorldStore((state) => state.modelSettings);
  const [instruction, setInstruction] = useState(String(card.config.system_instruction ?? ""));
  const [model, setModel] = useState(String(card.config.model ?? "gemini-3.7-flash"));
  const prompt = useNodeSurfaceStore((state) => state.drafts[card.id] ?? String(card.config.prompt ?? ""));
  const setDraft = useNodeSurfaceStore((state) => state.setDraft);

  useEffect(() => setInstruction(String(card.config.system_instruction ?? "")), [card.config.system_instruction]);
  useEffect(() => setModel(String(card.config.model ?? "gemini-3.7-flash")), [card.config.model]);

  const [capabilities, setCapabilities] = useState<{ id: string; target_name: string; description: string; kind: string }[]>([]);
  const [capabilityError, setCapabilityError] = useState("");
  const socketState = useWorldStore((state) => state.socketState);
  useEffect(() => {
    if (level !== "workspace" || card.ephemeral) return;
    let active = true;
    const timer = setTimeout(() => {
      worldApi.getAgentCapabilities(card.id).then((result) => {
        if (active) { setCapabilities(result.capabilities); setCapabilityError(""); }
      }).catch((error) => { if (active) setCapabilityError(apiErrorMessage(error)); });
    }, 100);
    return () => { active = false; clearTimeout(timer); };
  }, [card.id, card.ephemeral, level, cards, edges, socketState]);
  const output = Array.isArray(card.config.output)
    ? card.config.output.map(String)
    : [];
  const modelOptions = [...new Set([model, ...modelSettings.models].filter(Boolean))];

  return (
    <div className="expanded-stack">
      {level === "workspace" && <><label className="field-label"><span>When to use this Agent</span><textarea defaultValue={String(card.config.description ?? "")} maxLength={500}
        onBlur={(event) => { if (event.target.value !== card.config.description) void updateCard(card.id, { config: { description: event.target.value } }); }} /></label>
      {cards.find((c) => c.id === card.parent_id)?.type === "legion" && <section className="card-section">
        <div className="section-heading"><span>Legion: {cards.find((c) => c.id === card.parent_id)?.name ?? "Team"}</span></div>
        <label className="field-label"><span>Member role</span><input key={String(card.config.legion_role ?? "")} defaultValue={String(card.config.legion_role ?? "")} maxLength={200} placeholder="Planner, executor, reviewer"
          onBlur={(e) => { if (e.target.value !== card.config.legion_role) void updateCard(card.id, { config: { legion_role: e.target.value } }); }} /></label>
        <label><input type="checkbox" checked={card.config.inherit_legion_model !== false}
          onChange={(e) => void updateCard(card.id, { config: { inherit_legion_model: e.target.checked } })} /> Use team model override</label>
        <p>Team instructions and shared state are included at the start of each Run.</p>
      </section>}
      </>}
      <div className="field-row">
        <label>
          <span>Model</span>
          <select
            value={model}
            onChange={(event) => {
              const value = event.target.value;
              setModel(value);
              if (value !== card.config.model) {
                void updateCard(card.id, { config: { model: value } });
              }
            }}
          >
            {modelOptions.map((option) => <option key={option} value={option}>{option}</option>)}
          </select>
        </label>
        <div className="live-readout">
          <Radio size={13} aria-hidden="true" />
          <span title="Google ADK selects the model adapter automatically.">ADK · {card.status}</span>
        </div>
      </div>

      <label className="field-label">
        <span>System instruction</span>
        <textarea
          value={instruction}
          rows={3}
          onChange={(event) => setInstruction(event.target.value)}
          onBlur={() => {
            if (instruction !== card.config.system_instruction) {
              void updateCard(card.id, { config: { system_instruction: instruction } });
            }
          }}
        />
      </label>

      {level === "workspace" && <section className="card-section">
        <div className="section-heading">
          <span>Effective capabilities</span>
        </div>
        <div className="capability-chips">
          {capabilities.length > 0 ? capabilities.map((capability) => (
            <span key={capability.id} title={capability.description}>{capability.target_name} · {capability.kind}</span>
          )) : <em>Equip a resource or connect a shared resource to grant a scoped tool.</em>}
          {capabilityError && <p role="alert">{capabilityError}</p>}
        </div>
      </section>}

      <label className="field-label prompt-field">
        <span>Prompt</span>
        <textarea
          value={prompt}
          rows={2}
          placeholder="Ask Atlas to work with its connected objects…"
          onChange={(event) => setDraft(card.id, event.target.value)}
          onKeyDown={(event) => {
            if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && prompt.trim()) {
              void runAgent(card.id, prompt.trim());
            }
          }}
        />
      </label>

      <div className="action-row">
        <button
          type="button"
          className="primary-button"
          onClick={() => void runAgent(card.id, prompt.trim())}
          disabled={!prompt.trim() || card.status === "running"}
        >
          <Play size={14} fill="currentColor" /> Run agent
        </button>
        <button
          type="button"
          className="secondary-button"
          onClick={() => void stopAgent(card.id)}
          disabled={card.status !== "running" && card.status !== "waiting"}
        >
          <CircleStop size={14} /> Stop
        </button>
      </div>

      {level === "workspace" && <section className="card-section output-section">
        <div className="section-heading"><span>Runtime activity</span><small>operational log</small></div>
        <InstrumentOutput lines={output} empty="Run output and tool activity will appear here." />
      </section>}
    </div>
  );
}
