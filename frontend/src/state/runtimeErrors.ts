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
      detail: 'Open Settings → Models to check the API key and selected model, then retry your request.',
    };
  }
  if (/unauthori[sz]ed|authentication|401|403|api.?key|credentials|model connection.*missing/i.test(detail)) {
    return { title: 'Model connection needs attention', detail: 'Open Settings → Models to check the API key and selected model, then retry your request.' };
  }
  if (/429|rate.?limit|quota/i.test(detail)) return { title: 'Model service limit reached', detail: 'Wait and retry, or check the usage limit with your model service.' };
  if (/timeout|timed out|connection|network|503|502/i.test(detail)) return { title: 'Model service unavailable', detail: 'Check the connection and retry when the service is available. Completed actions may already have taken effect.' };
  return {
    title: "Agent run failed",
    detail: `${model}: ${detail.slice(0, 500)}`,
  };
}
