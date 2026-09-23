import type { FrontendPlugin } from "@oaw/plugin-api";
import * as facts from "./facts";
import * as vectors from "./vectors";
import * as ontology from "./ontology";
import * as structures from "./structures";
import "./style.css";

// Each store module exports Preview and Workspace; view names match the node type's frontend map.
export default { apiVersion: 1, views: {
  "facts-preview": facts.Preview, facts: facts.Workspace,
  "vectors-preview": vectors.Preview, vectors: vectors.Workspace,
  "ontology-preview": ontology.Preview, ontology: ontology.Workspace,
  "structures-preview": structures.Preview, structures: structures.Workspace,
} } satisfies FrontendPlugin;
