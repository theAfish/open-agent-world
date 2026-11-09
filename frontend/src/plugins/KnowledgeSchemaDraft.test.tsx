// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SchemaEditor } from "../../../plugins/knowledge_base/frontend/SchemaEditor";
import { draftPrompt } from "../../../plugins/knowledge_base/frontend/SchemaDraftChat";
import type { DraftApi } from "../../../plugins/knowledge_base/frontend/SchemaDraftChat";

afterEach(cleanup);

const setup = (api: Partial<DraftApi>) => {
  const full: DraftApi = { agentName: "Lab", convert: vi.fn(), send: vi.fn().mockResolvedValue(undefined), ...api };
  render(<SchemaEditor busy={false} schemas={[]} kind="experiment" call={vi.fn()} perform={work => work()}
    onSaved={vi.fn()} draft={full} />);
  fireEvent.click(screen.getByRole("button", { name: /New schema/ }));
  return full;
};

const attach = (name: string) => fireEvent.change(screen.getByLabelText("Attach sample files"),
  { target: { files: [new File(["a,b\n1,2"], name, { type: "text/csv" })] } });

describe("Draft with AI", () => {
  it("hands the request and converted samples to the connected agent and closes the editor", async () => {
    const send = vi.fn().mockResolvedValue(undefined);
    setup({ convert: vi.fn().mockResolvedValue({ markdown: "| a | b |", truncated: false }), send });
    expect(screen.getByText("Lab")).toBeInTheDocument();
    attach("log.csv");
    await screen.findByText("ready");
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "one batch per row" } });
    fireEvent.click(screen.getByRole("button", { name: /Send/ }));
    await waitFor(() => expect(screen.queryByLabelText("Message")).not.toBeInTheDocument());
    expect(send).toHaveBeenCalledWith({ message: "one batch per row",
      samples: [{ filename: "log.csv", markdown: "| a | b |" }], current: undefined });
  });

  it("keeps a failed conversion visible and the editor open when the agent cannot be started", async () => {
    const send = vi.fn().mockRejectedValue(new Error("Agent is busy"));
    setup({ convert: vi.fn().mockRejectedValue(new Error("Sample files are limited to 2 MiB")), send });
    attach("scan.pdf");
    await screen.findByText("Sample files are limited to 2 MiB");
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "draft it" } });
    fireEvent.click(screen.getByRole("button", { name: /Send/ }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Agent is busy"));
    expect(send.mock.calls[0][0].samples).toEqual([]);
    expect(screen.getByLabelText("Message")).toBeEnabled();
  });

  it("names an existing schema so the agent updates it", () => {
    expect(draftPrompt("kb-1", { message: "add moisture", samples: [], current: { schema_id: "s-1", name: "Old" } }))
      .toContain("operation=update with schema_id=s-1");
    expect(draftPrompt("kb-1", { message: "new", samples: [{ filename: "a.csv", markdown: "| x |" }] }))
      .toMatch(/operation=create[\s\S]*<sample_file name="a.csv">/);
  });
});
