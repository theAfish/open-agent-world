// @vitest-environment jsdom
import {afterEach,expect,it,vi} from "vitest";
import {cleanup,fireEvent,render,screen,waitFor} from "@testing-library/react";
import {PdfImportChoice} from "./PdfImportChoice";

afterEach(()=>{cleanup();vi.unstubAllGlobals();});
function setup(){
  HTMLDialogElement.prototype.showModal=function(){this.open=true;};HTMLDialogElement.prototype.close=function(){this.open=false;};
  vi.stubGlobal("fetch",vi.fn().mockResolvedValue({ok:true,json:async()=>({papers:[{id:"metadata",title:"Existing paper",doi:"10.1234/test",has_pdf:false},{id:"with-pdf",title:"Read paper",has_pdf:true}],scopes:[]})}));
}
it("requires explicit association instead of guessing from filename",async()=>{
  setup();const done=vi.fn();render(<PdfImportChoice file={new File(["pdf"],"Existing paper.pdf")} onDone={done}/>);
  await waitFor(()=>expect(screen.getByText("Existing paper · 10.1234/test")).toBeTruthy());
  const select=screen.getByRole("combobox");expect((select as HTMLSelectElement).value).toBe("");
  fireEvent.change(select,{target:{value:"metadata"}});fireEvent.submit(screen.getByRole("button",{name:"确认导入"}).closest("form")!);
  expect(done).toHaveBeenCalledWith({paperId:"metadata",kind:"main"});
});
it("keeps an explicit drop target and offers an independent supplement",async()=>{
  setup();const done=vi.fn();render(<PdfImportChoice file={new File(["pdf"],"si.pdf")} targetPaperId="with-pdf" onDone={done}/>);
  await screen.findByText("Read paper");fireEvent.change(screen.getByRole("combobox"),{target:{value:"supplement"}});
  fireEvent.submit(screen.getByRole("button",{name:"确认导入"}).closest("form")!);expect(done).toHaveBeenCalledWith({paperId:"with-pdf",kind:"supplement"});
});
