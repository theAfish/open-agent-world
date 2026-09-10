import { mount, unmount } from "svelte";
import StructureHost from "./StructureHost.svelte";
import { parse_structure_file } from "matterviz/structure/parse";

export function renderStructure(target: HTMLElement, file: { name: string; data: string }, onError: (message: string) => void) {
  const bytes = Uint8Array.from(atob(file.data), character => character.charCodeAt(0));
  const structure = parse_structure_file(new TextDecoder("utf-8", { fatal: true }).decode(bytes), file.name);
  if (!structure?.sites?.length) throw new Error("No atoms found in this structure file.");
  if (structure.sites.length > 20000) throw new Error("This viewer supports up to 20,000 atoms per structure.");
  const component = mount(StructureHost, { target, props: { structure, onError } });
  target.dataset.atomCount = String(structure.sites.length);
  return () => { delete target.dataset.atomCount; void unmount(component); };
}
