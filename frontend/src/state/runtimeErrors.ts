export interface RuntimeErrorNotice {
  title: string;
  detail: string;
}

function configuredCredential(error: string, model: string): string | undefined {
  const fromError = error.match(/set the\s+([A-Z][A-Z0-9_]*_API_KEY)\b/i)?.[1];
  if (fromError) return fromError.toUpperCase();
  const provider = model.split("/", 1)[0]?.toLowerCase();
  if (provider === "openai") return "OPENAI_API_KEY";
  if (provider === "anthropic") return "ANTHROPIC_API_KEY";
  return undefined;
}

export function describeRuntimeError(error: unknown, model = "configured model"): RuntimeErrorNotice {
  const detail = String(error || "The runtime returned an unknown error.").trim();
  const credential = configuredCredential(detail, model);
  if (credential && /missing credentials|api[_ ]key|authenticate/i.test(detail)) {
    return {
      title: "Model credentials unavailable",
      detail: `ADK could not authenticate ${model}. Set ${credential} in the terminal that runs scripts/dev.ps1, then restart the development server.`,
    };
  }
  return {
    title: "ADK agent run failed",
    detail: `${model}: ${detail.slice(0, 500)}`,
  };
}
