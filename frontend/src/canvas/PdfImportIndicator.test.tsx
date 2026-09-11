// @vitest-environment jsdom
import {cleanup,render,screen} from "@testing-library/react";
import {afterEach,expect,it,vi} from "vitest";
import {PdfImportIndicator} from "./PdfImportIndicator";
afterEach(cleanup);
it("shows upload percentage separately from processing and completion",()=>{
  const {rerender}=render(<PdfImportIndicator status="1 / 2 · notes.pdf" progress={{stage:"uploading",percent:45}} onDismiss={vi.fn()} />);
  expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("45");
  expect(screen.getByText("45%")).toBeTruthy();
  rerender(<PdfImportIndicator status="1 / 2 · notes.pdf" progress={{stage:"processing",percent:100}} onDismiss={vi.fn()} />);
  expect(screen.getByText("上传完成 · 正在解析 PDF")).toBeTruthy();
  expect(screen.queryByRole("button")).toBeNull();
  rerender(<PdfImportIndicator status="已导入 2 篇 PDF" onDismiss={vi.fn()} />);
  expect(screen.queryByRole("progressbar")).toBeNull();
  expect(screen.getByRole("button")).toBeTruthy();
});
