import { t, useLocale } from "../i18n";
import { FolderOpen } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { worldApi } from "../api/client";

export function FolderPathInput({ value, onChange, disabled, label, describedBy, placeholder, onPickingChange, kind = "folder" }: {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  label: string;
  describedBy?: string;
  placeholder?: string;
  onPickingChange?: (picking: boolean) => void;
  kind?: "folder" | "file" | "path";
}) {
  useLocale();
  const [picking, setPicking] = useState(false);
  const [error, setError] = useState("");
  const [choicesOpen, setChoicesOpen] = useState(false);
  const [pickingKind, setPickingKind] = useState<"folder" | "file">("folder");
  const alive = useRef(true);
  const errorId = useId();
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; onPickingChange?.(false); };
  }, [onPickingChange]);

  const browse = async (selectedKind: "folder" | "file") => {
    if (picking || disabled) return;
    setChoicesOpen(false);
    setPickingKind(selectedKind);
    setPicking(true);
    setError("");
    onPickingChange?.(true);
    try {
      const result = await (selectedKind === "file" ? worldApi.pickFile(value.trim() || null) : worldApi.pickFolder(value.trim() || null));
      if (alive.current && result.path !== null) onChange(result.path);
    } catch (cause) {
      if (alive.current) setError(cause instanceof Error ? cause.message : t("Could not open folder selection. Enter the path manually."));
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
      <button type="button" className="secondary-button" aria-label={t("Browse for {v0}", { v0: String(label) })} disabled={disabled || picking}
        aria-expanded={kind === "path" ? choicesOpen : undefined}
        onClick={() => kind === "path" ? setChoicesOpen(!choicesOpen) : void browse(kind)}>
        <FolderOpen size={13} /> {picking ? t("Selecting…") : t("Browse…")}
      </button>
    </div>
    {kind === "path" && choicesOpen && <div className="path-picker-choices" role="group" aria-label={t("Choose a file or folder")}>
      <button type="button" className="secondary-button" disabled={disabled || picking} onClick={() => void browse("file")}>{t("Choose file")}</button>
      <button type="button" className="secondary-button" disabled={disabled || picking} onClick={() => void browse("folder")}>{t("Choose folder")}</button>
    </div>}
    {picking && <small role="status">{pickingKind === "file" ? t("Choose a file in the system window.") : t("Choose a folder in the system window.")}</small>}
    {error && <small role="alert" id={errorId} className="settings-error">{error}</small>}
  </div>;
}
