import { t, useLocale } from "../i18n";
import { Download, File, FilePlus2, Folder, FolderPlus, Trash2, Upload } from "lucide-react";
import { useState } from "react";

export interface SkillAsset { data_base64: string; media_type: string }
export type SkillFiles = Record<string, string | SkillAsset>;
const parentPath = (path: string) => path.split("/").slice(0, -1).join("/");
const basename = (path: string) => path.split("/").at(-1)!;

async function readAsset(file: File): Promise<string | SkillAsset> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    if (!text.includes("\0") && (!file.type || file.type.startsWith("text/") || /json|javascript|xml|svg/.test(file.type))) return text;
  } catch { /* Preserve non-UTF-8 files as bytes. */ }
  const data_base64 = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1]);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
  return { data_base64, media_type: file.type || "application/octet-stream" };
}

function downloadFile(path: string, content: string | SkillAsset) {
  const blob = typeof content === "string" ? new Blob([content], { type: "text/plain;charset=utf-8" })
    : new Blob([Uint8Array.from(atob(content.data_base64), (character) => character.charCodeAt(0))], { type: content.media_type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a"); link.href = url; link.download = basename(path); link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function SkillFilesEditor({ files, directories, onChange, onInstructions, onError, onBusy }: {
  files: SkillFiles; directories: string[];
  onChange: (files: SkillFiles, directories: string[]) => void;
  onInstructions: (text: string) => void; onError: (message: string) => void; onBusy: (busy: boolean) => void;
}) {
  useLocale();
  const [folder, setFolder] = useState("");
  const [newName, setNewName] = useState("");
  const [selected, setSelected] = useState<string>();
  const [rename, setRename] = useState("");
  const folders = new Set(directories);
  for (const path of [...Object.keys(files), ...directories]) {
    const parts = path.split("/");
    for (let index = 1; index < parts.length; index++) folders.add(parts.slice(0, index).join("/"));
  }
  const atFolder = (name: string) => [folder, name].filter(Boolean).join("/");
  const available = (path: string, original?: string) => {
    if (!path || path === "SKILL.md" || path.split("/").some((part) => !part || part === "." || part === "..") || /[\\:\0]/.test(path)) {
      onError(t("Use a relative path such as scripts/build.py. Edit SKILL.md in Instructions.")); return false;
    }
    if (path !== original && (Object.hasOwn(files, path) || folders.has(path))) { onError(t("“{v0}” already exists. Open it to edit its contents.", { v0: String(path) })); return false; }
    return true;
  };
  const open = (path: string) => { setSelected(path); setRename(path); };
  const add = (directory: boolean) => {
    const path = atFolder(newName.trim());
    if (!newName.trim() || !available(path)) return;
    if (directory) { onChange(files, [...directories, path]); setFolder(path); setSelected(undefined); }
    else { onChange({ ...files, [path]: "" }, directories); open(path); }
    setNewName(""); onError("");
  };
  const importFiles = async (incoming: File[], wholeFolder = false) => {
    onBusy(true); onError("");
    try {
      const entries = await Promise.all(incoming.map(async (file) => {
        // Selecting a skill folder uses that folder's contents as the root.
        const relative = wholeFolder ? file.webkitRelativePath.split("/").slice(1).join("/") : file.name;
        return [atFolder(relative), await readAsset(file)] as const;
      }));
      const instructions = entries.find(([path]) => path === "SKILL.md");
      const resources = entries.filter(([path]) => path !== "SKILL.md");
      if (resources.some(([path]) => !available(path))) return;
      if (instructions && typeof instructions[1] === "string") onInstructions(instructions[1]);
      onChange({ ...files, ...Object.fromEntries(resources) }, directories);
      if (resources[0]) { setFolder(parentPath(resources[0][0])); open(resources[0][0]); }
    } catch (error) { onError(error instanceof Error ? error.message : String(error)); }
    finally { onBusy(false); }
  };
  const content = selected === undefined ? undefined : files[selected];
  return <section className="skill-files-editor" aria-label={t("Skill files")}>
    <p className="toolbox-help">{t("Organize scripts, assets, references, or your own folders. Import a skill folder to keep its directory structure. Toolbox limit: 16 MiB including file data.")}</p>
    <div className="skill-files-location"><Folder size={15} /><select aria-label={t("Current folder")} value={folder} onChange={(event) => { setFolder(event.target.value); setSelected(undefined); }}>
      <option value="">{t("Skill root /")}</option>{[...folders].sort().map((path) => <option key={path} value={path}>{path}/</option>)}
    </select>{folder && <button type="button" className="skill-file-icon" aria-label={t("Remove empty folder")} disabled={Object.keys(files).some((path) => path.startsWith(folder + "/")) || [...folders].some((path) => path.startsWith(folder + "/"))} onClick={() => { onChange(files, directories.filter((path) => path !== folder)); setFolder(""); }}><Trash2 size={14} /></button>}</div>
    <div className="skill-files-create"><input aria-label={t("New file or folder name")} placeholder={t("e.g. scripts or build.py")} value={newName} onChange={(event) => setNewName(event.target.value)} />
      <button type="button" className="secondary-button" aria-label={t("Create folder")} disabled={!newName.trim()} onClick={() => add(true)}><FolderPlus size={14} /></button>
      <button type="button" className="secondary-button" aria-label={t("Create text file")} disabled={!newName.trim()} onClick={() => add(false)}><FilePlus2 size={14} /></button></div>
    <div className="skill-files-import">
      <label className="toolbox-file-input"><Upload size={14} /> {t("Import files")}<input type="file" multiple aria-label={t("Import skill files")} onChange={(event) => { if (event.target.files) void importFiles(Array.from(event.target.files)); event.target.value = ""; }} /></label>
      <label className="toolbox-file-input"><FolderPlus size={14} /> {t("Import folder")}<input type="file" multiple {...{ webkitdirectory: "" }} aria-label={t("Import skill folder")} onChange={(event) => { if (event.target.files) void importFiles(Array.from(event.target.files), true); event.target.value = ""; }} /></label>
    </div>
    <div className="skill-file-list">
      {[...folders].filter((path) => parentPath(path) === folder).sort().map((path) => <button type="button" key={path} className="skill-file-row" onClick={() => { setFolder(path); setSelected(undefined); }}><Folder size={14} />{basename(path)}/</button>)}
      {Object.keys(files).filter((path) => parentPath(path) === folder).sort().map((path) => <button type="button" key={path} className={`skill-file-row ${selected === path ? "is-selected" : ""}`} aria-label={t("Open file {v0}", { v0: String(path) })} onClick={() => open(path)}><File size={14} />{basename(path)}</button>)}
      {!Object.keys(files).some((path) => parentPath(path) === folder) && ![...folders].some((path) => parentPath(path) === folder) && <p className="toolbox-help">{t("This folder is empty. Create a file or import files here.")}</p>}
    </div>
    {selected !== undefined && content !== undefined && <div className="skill-file-details">
      <label>{t("File path")}<input aria-label={t("File path")} value={rename} onChange={(event) => setRename(event.target.value)} /></label>
      <div className="skill-file-actions"><button type="button" className="secondary-button" disabled={rename === selected} onClick={() => {
        const path = rename.trim(); if (!available(path, selected)) return;
        const next = { ...files }; delete next[selected]; next[path] = content;
        onChange(next, [...folders]); setFolder(parentPath(path)); open(path); onError("");
      }}>{t("Move / rename")}</button><button type="button" className="secondary-button" onClick={() => downloadFile(selected, content)}><Download size={13} /> {t("Download file")}</button>
        <button type="button" className="skill-file-icon" aria-label={t("Delete file {v0}", { v0: String(selected) })} onClick={() => { const next = { ...files }; delete next[selected]; onChange(next, [...folders]); setSelected(undefined); }}><Trash2 size={14} /></button></div>
      {typeof content === "string" ? <label>{t("File contents")}<textarea aria-label={t("File contents")} className="toolbox-instructions" rows={10} value={content} onChange={(event) => onChange({ ...files, [selected]: event.target.value }, directories)} /></label>
        : <div className="skill-binary-preview">{["image/png", "image/jpeg", "image/gif", "image/webp"].includes(content.media_type) && <img src={`data:${content.media_type};base64,${content.data_base64}`} alt={basename(selected)} />}<span>{content.media_type} · {Math.round(atob(content.data_base64).length / 1024 * 10) / 10} {t("KiB")}</span></div>}
    </div>}
  </section>;
}
