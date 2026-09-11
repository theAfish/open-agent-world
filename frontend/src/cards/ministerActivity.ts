export function ministerToolSummary(content: string, kind: string): string {
  const name = content.split("\n")[0].replace(/^(Using|Finished) /, "");
  const labels: Record<string, [string, string]> = {
    canvas_inspect: ["Checking this area", "Area checked"], canvas_create: ["Creating a card", "Card created"],
    canvas_move: ["Moving a card", "Card moved"], canvas_rename: ["Renaming a card", "Card renamed"],
    canvas_connect: ["Connecting cards", "Cards connected"], canvas_disconnect: ["Disconnecting cards", "Cards disconnected"],
    canvas_update: ["Updating cards", "Cards updated"], canvas_delete: ["Reviewing deletion", "Deletion reviewed"],
    canvas_organize: ["Organizing cards", "Cards organized"],
  };
  const label = labels[name];
  if (!label) return "Canvas activity";
  try {
    const result = JSON.parse(content.slice(content.indexOf("\n\n") + 2));
    if (result?.ok === false) return `${label[0]}: needs attention`;
    if (result?.status === 'confirmation_required' || result?.result?.status === 'confirmation_required') return 'Waiting for your confirmation';
  } catch { /* Activity may have no structured detail. */ }
  return label[kind === "tool_started" ? 0 : 1];
}

