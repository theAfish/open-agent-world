import type { Component } from "svelte";
import type { AnyStructure } from "matterviz/structure";
declare const StructureHost: Component<{ structure: AnyStructure; onError: (message: string) => void }>;
export default StructureHost;
