export type PdfTextRange = {textItemIndex:number;startOffset:number;endOffset:number};
export type GlyphRect = {x:number;y:number;width:number;height:number};

/** Exact DOM Range geometry; never estimate proportional glyph widths. */
export function measureTextRange(source:PdfTextRange,page:HTMLElement,textLayer:HTMLElement):GlyphRect[] {
  const span=textLayer.querySelector<HTMLElement>(`[data-text-item-index="${source.textItemIndex}"]`);
  const bounds=page.getBoundingClientRect();
  if(!span||!bounds.width||!bounds.height)return [];
  const walker=document.createTreeWalker(span,NodeFilter.SHOW_TEXT);
  let offset=0,node:Node|null,start:{node:Node;offset:number}|undefined,end:{node:Node;offset:number}|undefined;
  while((node=walker.nextNode())) {
    const length=node.textContent?.length??0;
    if(!start&&source.startOffset>=offset&&source.startOffset<offset+length)start={node,offset:source.startOffset-offset};
    if(source.endOffset>offset&&source.endOffset<=offset+length){end={node,offset:source.endOffset-offset};break;}
    offset+=length;
  }
  if(!start||!end)return [];
  const range=document.createRange();range.setStart(start.node,start.offset);range.setEnd(end.node,end.offset);
  return Array.from(range.getClientRects()).flatMap(rect=>{
    const left=Math.max(bounds.left,rect.left),top=Math.max(bounds.top,rect.top);
    const right=Math.min(bounds.right,rect.right),bottom=Math.min(bounds.bottom,rect.bottom);
    return right>left&&bottom>top?[{x:(left-bounds.left)/bounds.width,y:(top-bounds.top)/bounds.height,width:(right-left)/bounds.width,height:(bottom-top)/bounds.height}]:[];
  });
}
