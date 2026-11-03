// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SchemaEditor } from "../../../plugins/knowledge_base/frontend/SchemaEditor";
import type { DraftApi } from "../../../plugins/knowledge_base/frontend/SchemaDraftChat";

afterEach(cleanup);

const draft = { name: "Batch", description: "One batch", system_prompt: "One row is one batch.",
  definition: { required: ["batch_no"], properties: { batch_no: { value: "string", description: "Batch id" } } } };

const setup = (api: Partial<DraftApi>) => {
  const full: DraftApi = { ready: true, modelSelect: null, convert: vi.fn(), send: vi.fn(), ...api };
  render(<SchemaEditor busy={false} schemas={[]} kind="experiment" call={vi.fn()} perform={work => work()}
    onSaved={vi.fn()} draft={full} />);
  fireEvent.click(screen.getByRole("button", { name: /New schema/ }));
  return full;
};

const attach = (name: string) => fireEvent.change(screen.getByLabelText("Attach sample files"),
  { target: { files: [new File(["a,b\n1,2"], name, { type: "text/csv" })] } });

describe("Draft with AI", () => {
  it("sends the request with converted samples, applies the draft to the editor, and undoes it", async () => {
    const send = vi.fn().mockResolvedValue({ reply: "Drafted a batch schema.", draft });
    setup({ convert: vi.fn().mockResolvedValue({ markdown: "| a | b |", truncated: false }), send });
    attach("log.csv");
    await screen.findByText("ready");
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "one batch per row" } });
    fireEvent.click(screen.getByRole("button", { name: /Send/ }));
    await screen.findByText("Drafted a batch schema.");
    expect(send).toHaveBeenCalledWith({ messages: [{ role: "user", content: "one batch per row" }],
      samples: [{ filename: "log.csv", markdown: "| a | b |" }], current: undefined });

    fireEvent.click(screen.getByRole("button", { name: "Apply to editor" }));
    expect(screen.getAllByLabelText("Name")[0]).toHaveValue("Batch");
    expect(screen.getByLabelText("System prompt")).toHaveValue("One row is one batch.");
    expect(screen.getAllByLabelText("Name")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: /Undo apply/ }));
    expect(screen.getAllByLabelText("Name")[0]).toHaveValue("");
    expect(screen.getAllByLabelText("Name")).toHaveLength(3);
  });

  it("keeps a failed conversion visible, lets the person continue, and reports a failed draft", async () => {
    const send = vi.fn().mockRejectedValue(new Error("The model's draft was not usable: no properties"));
    setup({ convert: vi.fn().mockRejectedValue(new Error("Sample files are limited to 2 MiB")), send });
    attach("scan.pdf");
    await screen.findByText("Sample files are limited to 2 MiB");
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "draft it" } });
    fireEvent.click(screen.getByRole("button", { name: /Send/ }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("no properties"));
    expect(send.mock.calls[0][0].samples).toEqual([]);
    expect(screen.getByLabelText("Message")).toBeEnabled();
  });

  it("does not send without a model", () => {
    setup({ ready: false });
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "draft it" } });
    expect(screen.getByRole("button", { name: /Send/ })).toBeDisabled();
  });
});
