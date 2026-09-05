import { FolderOpen } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { worldApi } from "../api/client";

export function FolderPathInput({ value, onChange, disabled, label, describedBy, placeholder, onPickingChange }: {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  label: string;
  describedBy?: string;
  placeholder?: string;
  onPickingChange?: (picking: boolean) => void;
}) {
  const [picking, setPicking] = useState(false);
  const [error, setError] = useState("");
  const alive = useRef(true);
  const errorId = useId();
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; onPickingChange?.(false); };
  }, [onPickingChange]);

  const browse = async () => {
    if (picking || disabled) return;
    setPicking(true);
    setError("");
    onPickingChange?.(true);
    try {
      const result = await worldApi.pickFolder(value.trim() || null);
      if (alive.current && result.path !== null) onChange(result.path);
    } catch (cause) {
      if (alive.current) setError(cause instanceof Error ? cause.message : "Could not open folder selection. Enter the path manually.");
    } finally {
      if (alive.current) {
        setPicking(false);
        onPickingChange?.(false);
      }
    }
  };

  return <div className="folder-path-control">
    <div className="folder-path-row">
      <input aria-label={label} aria-describedby={[describedBy, error ? errorId : null].filter(Boolean).join(" ") || undefined}
        value={value} disabled={disabled || picking} placeholder={placeholder} spellCheck={false} autoComplete="off"
        onChange={(event) => { setError(""); onChange(event.target.value); }} />
      <button type="button" className="secondary-button" aria-label={`Browse for ${label}`} disabled={disabled || picking} onClick={() => void browse()}>
        <FolderOpen size={13} /> {picking ? "Selecting…" : "Browse…"}
      </button>
    </div>
    {picking && <small role="status">Choose a folder in the system window.</small>}
    {error && <small role="alert" id={errorId} className="settings-error">{error}</small>}
  </div>;
}
