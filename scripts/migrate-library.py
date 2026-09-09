"""Read-only legacy source -> fresh research world via public HTTP APIs.

Only run against an empty destination. Keeps old IDs, agent configuration,
paper bytes, reading progress, text resources and explicit relationships.
"""
import base64
import json
import os
from pathlib import Path
import sqlite3
import urllib.request
import urllib.error

root=Path(__file__).resolve().parents[1]
source=Path(os.environ["LOCALAPPDATA"])/"OpenAgentWorld"
library=root.parent/"图书馆"
base="http://127.0.0.1:8000/api"
def api(path, body=None):
    req=urllib.request.Request(base+path, data=None if body is None else json.dumps(body).encode(),headers={"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req,timeout=120) as response:return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{path}: {exc.read().decode()}") from exc

def main():
    if api("/nodes"):
        raise SystemExit("Destination must be empty; refusing duplicate migration")
    db=sqlite3.connect((source/"database/world.sqlite3").as_uri()+"?mode=ro",uri=True)
    db.row_factory=sqlite3.Row
    cards=[dict(r) for r in db.execute("SELECT * FROM cards")]
    edges=[dict(r) for r in db.execute("SELECT * FROM edges")]
    report={"source":str(source),"nodes":[],"skipped":[],"errors":[]}
    configs={c["id"]:json.loads(c["config_json"]) for c in cards}
    contains={}
    # Older builds encode membership in library.contains; resolve either schema.
    for e in edges:
        rel=e.get("relationship",e.get("type",e.get("relationship_type")))
        if rel=="library.contains": contains[e.get("target_id",e.get("target"))]=e.get("source_id",e.get("source"))
    for c in sorted(cards,key=lambda c:c["type"]!="library.region"):
        cfg=configs[c["id"]]; kind=c["type"]
        if kind not in {"library.region","library.paper","agent","text"}:
            report["skipped"].append({"id":c["id"],"type":kind});continue
        parent=contains.get(c["id"])
        position={"x":c["x"],"y":c["y"]}
        if parent:
            owner=next(n for n in cards if n["id"]==parent)
            local=configs[parent].get("member_layouts",{}).get(c["id"],position)
            position={"x":owner["x"]+local["x"]+24,"y":owner["y"]+local["y"]+150}
        payload={"id":c["id"],"type":kind,"name":c["name"],"position":position,"config":cfg}
        if parent:payload["parent_id"]=parent
        if kind=="library.region":
            payload["config"]={"description":cfg.get("summary",cfg.get("topic",""))}
            payload["size"]={"width":max(1100,c["width"]),"height":max(800,c["height"])}
        if kind=="library.paper":payload["config"]={k:str(cfg.get(k,"")) for k in ("authors","year","doi")}
        if kind=="agent":payload["status"]="idle"
        if kind=="text":
            payload["content"]=cfg.get("content",cfg.get("preview",""))
            resource=db.execute("SELECT relative_path FROM resources WHERE card_id=?",(c["id"],)).fetchone()
            if resource:payload["content"]=(source/resource["relative_path"]).read_text(encoding="utf-8")
        try:
            api("/nodes",payload)
            report["nodes"].append(c["id"])
            if kind=="library.paper":
                identifier=cfg.get("library_id","")
                pdf=source/"assets/library/pdfs"/f"{identifier}.pdf" if cfg.get("storage")=="managed" else library/"files"/f"{identifier}.pdf"
                data=api(f'/nodes/{c["id"]}/document')
                data=api(f'/nodes/{c["id"]}/actions/import',{"arguments":{"filename":cfg.get("filename") or pdf.name,"pdf":base64.b64encode(pdf.read_bytes()).decode()},"expected_revision":data["revision"]})
                api(f'/nodes/{c["id"]}/actions/annotate',{"arguments":{"page":max(1,min(cfg.get("last_page",1),data["value"]["pages"]))},"expected_revision":data["revision"]})
        except Exception as exc:report["errors"].append({"id":c["id"],"error":str(exc)})
    for e in edges:
        rel=e.get("relationship",e.get("type",e.get("relationship_type")))
        if rel=="library.contains":continue
        s=e.get("source_id",e.get("source"));t=e.get("target_id",e.get("target"))
        if s not in report["nodes"] or t not in report["nodes"]:continue
        try:api("/edges",{"source":s,"target":t,"relationship":rel,"direction":e.get("direction","forward")})
        except Exception as exc:report["errors"].append({"edge":e["id"],"error":str(exc)})
    api("/nodes",{"type":"xrd.analysis","name":"XRD / PyWPEM","position":{"x":-450,"y":0}})
    destination=root/".open-agent-world/migration-report.json"
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
