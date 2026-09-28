import {describe,expect,it,vi} from "vitest";
vi.mock("@oaw/plugin-api",()=>({t:(text:string)=>text,useLocale:()=>"zh-CN"}));
import {studyRelationshipError,studyRelationshipPath,type StudyRelationship} from "../../../plugins/library/frontend/StudyCanvas";

const relation:StudyRelationship={id:"r1",source:"a",target:"b",type:"supports"};
const ids=new Set(["a","b","c"]);

describe("explicit study relationships",()=>{
  it("requires two current excerpts and prevents same-type duplicates without inferring reverse relations",()=>{
    expect(studyRelationshipError(relation,[],ids)).toBeNull();
    expect(studyRelationshipError({...relation,source:"missing"},[],ids)).toBeTruthy();
    expect(studyRelationshipError({...relation,target:"a"},[],ids)).toBeTruthy();
    expect(studyRelationshipError({...relation,id:"r2"},[relation],ids)).toBeTruthy();
    expect(studyRelationshipError({...relation,type:"contradicts"},[relation],ids)).toBeNull();
    expect(studyRelationshipError({...relation,id:"r2",source:"b",target:"a"},[relation],ids)).toBeNull();
    expect(studyRelationshipError({...relation,type:"guessed" as StudyRelationship["type"]},[],ids)).toBeTruthy();
  });

  it("retains the user's source-to-target direction and leaves layout coordinates untouched",()=>{
    const source=Object.freeze({x:10,y:20,width:240,height:120});
    const target=Object.freeze({x:510,y:220,width:240,height:160});
    const forward=studyRelationshipPath(source,target),reverse=studyRelationshipPath(target,source);
    expect(forward.path.startsWith("M 250 80 ")).toBe(true);
    expect(forward.path.endsWith(", 510 300")).toBe(true);
    expect(reverse.path.startsWith("M 510 300 ")).toBe(true);
    expect(reverse.path.endsWith(", 250 80")).toBe(true);
    expect(forward.x).toBe(380);expect(forward.y).toBe(190);
    expect(source).toEqual({x:10,y:20,width:240,height:120});
  });

  it("routes vertically for stacked cards and follows measured collapsed heights",()=>{
    const top={x:100,y:0,width:240,height:80},bottom={x:100,y:300,width:240,height:200};
    const path=studyRelationshipPath(top,bottom);
    expect(path.path.startsWith("M 220 80 ")).toBe(true);
    expect(path.path.endsWith(", 220 300")).toBe(true);
    expect(path.y).toBe(190);
    expect(studyRelationshipPath({...top,height:160},bottom).y).toBe(230);
    expect(studyRelationshipPath(bottom,top).path.endsWith(", 220 80")).toBe(true);
  });
});
