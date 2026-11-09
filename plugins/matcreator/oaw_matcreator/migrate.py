"""MatCreator Pack migration CLI; local/import use OAW's Python environment.

No MatCreator imports, dependency installation, model calls or source writes.
"""
from __future__ import annotations

import argparse
from contextlib import closing, nullcontext
from datetime import datetime, timezone
import fnmatch
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
from uuid import NAMESPACE_URL, uuid5
import zipfile


FORMAT = "oaw.matcreator-migration"
VERSION = 2
TRANSFER_RECEIPT = ".oaw-matcreator-transfer.json"
MAX_MANIFEST = 128 * 1024 * 1024
HISTORY_CHUNK_BYTES = 32 * 1024 * 1024
DISK_RESERVE_BYTES = 64 * 1024 * 1024
EXCLUDED = {".git", ".adk", ".venv", "venv", "__pycache__", "node_modules", ".cache", ".ssh"}


def identity(*parts):
    return str(uuid5(NAMESPACE_URL, json.dumps(parts, ensure_ascii=False)))


def timestamp(value=None):
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(value, (float, int)):
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).isoformat()


def json_value(value, default):
    return json.loads(value) if isinstance(value, str) and value else default if value is None else value


def read_database(path):
    uri = path.resolve().as_uri()
    if uri.startswith("file://") and not uri.startswith("file:///"):
        # SQLite's standard build rejects URI authorities. Keep a UNC path in
        # the filename component instead (file:////server/share/database).
        uri = "file:////" + uri[len("file://"):]
    connection = sqlite3.connect(uri + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")  # One consistent snapshot, including WAL records.
    return closing(connection)


def rows(connection, table):
    # Table names come only from the fixed readers below.
    return [dict(row) for row in connection.execute('SELECT * FROM "' + table + '"')]


def tables(connection):
    return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def linked(path):
    return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)


def relative_path(raw):
    path = PurePosixPath(raw)
    if (not raw or path.is_absolute() or any(p in {"", ".", ".."} for p in raw.split("/"))
            or any(c in raw for c in "\\:\x00") or str(path) != raw):
        raise ValueError(f"Unsafe relative path: {raw!r}")
    # Migration packages must also be safe to extract on Windows.
    for part in path.parts:
        if part.endswith((".", " ")) or part.split(".")[0].upper() in {
                "CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)],
                *[f"LPT{i}" for i in range(1, 10)]} or any(c in part for c in '<>"|?*'):
            raise ValueError(f"Non-portable path: {raw!r}")
    return path


def disk_directory(path):
    path = Path(path).expanduser().absolute()
    while not path.exists():
        if path.parent == path:
            raise ValueError("Destination volume is unavailable")
        path = path.parent
    return path if path.is_dir() else path.parent


def ensure_space(path, required):
    free = shutil.disk_usage(disk_directory(path)).free
    if free < required + DISK_RESERVE_BYTES:
        raise ValueError(f"Insufficient disk space: need {required:,} bytes plus a 64 MiB reserve; {free:,} bytes available")


def source_home(source):
    source = Path(source).expanduser().resolve(strict=True)
    if source.name == ".adk":
        return source.parent
    for home in (source, source / "agents" / "MatCreator"):
        if (home / ".adk").is_dir():
            return home
    raise ValueError("No .adk directory found. Pass the MatCreator data home or legacy agents/MatCreator folder.")


def wsl_location(raw):
    path = PureWindowsPath(raw)
    drive = path.drive.strip("\\").split("\\")
    if len(drive) == 2 and drive[0].casefold() in {"wsl.localhost", "wsl$"}:
        distribution = drive[1]
        if not distribution or distribution.startswith("-") or any(ord(c) < 32 for c in distribution):
            raise ValueError("Invalid WSL distribution in source path")
        return distribution, "/" + "/".join(path.parts[1:])
    return None


def wsl_call(distribution, arguments):
    result = subprocess.run(["wsl.exe", "-d", distribution, "--exec", *arguments],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise ValueError("Native WSL source operation failed: " + (result.stderr or result.stdout)[-2000:])
    return result


def wsl_argument(distribution, raw):
    raw = str(raw)
    other = wsl_location(raw)
    if other:
        if other[0] != distribution:
            raise ValueError("Source paths must use the same WSL distribution")
        return other[1]
    if raw.startswith("/"):
        return raw
    return wsl_call(distribution, ["wslpath", "-a", "-u", str(Path(raw).expanduser().absolute())]).stdout.strip()


def discover_wsl(source, workspace=None, *, user_id=None, selected=(), graph_paths=(), skills_dirs=(), old_workspaces=(), progress=None):
    distribution, native = wsl_location(source)
    script = wsl_argument(distribution, str(Path(__file__).resolve()))
    options = {"source": native, "workspace": wsl_argument(distribution, workspace) if workspace else None,
        "user_id": user_id, "selected": selected, "graph_paths": [wsl_argument(distribution, str(p)) for p in graph_paths],
        "skills_dirs": [wsl_argument(distribution, str(p)) for p in skills_dirs], "old_workspaces": old_workspaces}
    if progress:
        progress("Reading WSL metadata in its native SQLite environment; no database snapshot is created")
    manifest = json.loads(wsl_call(distribution, ["python3", "-B", script, "--read-source", json.dumps(options)]).stdout)
    native_home, native_workspace = manifest["source_home"], manifest["workspace"]
    def host_path(path):
        return str(PureWindowsPath("//wsl.localhost") / distribution / PurePosixPath(path).relative_to("/"))
    manifest["native_source"] = {"kind": "wsl", "distribution": distribution, "source_home": native_home}
    manifest["source_home"] = host_path(native_home)
    manifest["workspace"] = host_path(native_workspace)
    manifest["workspace_aliases"] = list(dict.fromkeys([native_workspace, *manifest["workspace_aliases"], manifest["workspace"]]))
    for skill in manifest["skills"]:
        skill["source_root"] = host_path(skill["source_root"])
        skill["source_path"] = host_path(skill["source_path"])
    for session in manifest["sessions"]:
        session["legacy"]["database"] = host_path(session["legacy"]["database"])
        if session["legacy"]["agent_graph_path"]:
            session["legacy"]["agent_graph_path"] = host_path(session["legacy"]["agent_graph_path"])
    return manifest


def artifact_paths(value, key=""):
    paths = set()
    if key in {"artifact_path", "plot_path", "structure_path", "artifact_paths", "plot_paths", "structure_paths"}:
        paths.update([value] if isinstance(value, str) else [v for v in value if isinstance(v, str)] if isinstance(value, list) else [])
    elif isinstance(value, dict):
        if key == "session_artifacts" or key.startswith("session_artifact__"):
            paths.update(v["path"] for v in value.values() if isinstance(v, dict) and isinstance(v.get("path"), str))
        for name, child in value.items():
            paths.update(artifact_paths(child, name))
    elif isinstance(value, list):
        for child in value:
            if key == "session_artifacts" and isinstance(child, dict) and isinstance(child.get("path"), str):
                paths.add(child["path"])
            paths.update(artifact_paths(child))
    return paths


def database_stamp(path):
    return [[p.stat().st_size, p.stat().st_mtime_ns] if p.exists() else None
        for p in (path, Path(str(path) + "-wal"))]


def read_sessions(home, user_id=None, selected=(), progress=None):
    path = home / ".adk" / "session.db"
    if not path.exists():
        return []
    stamp = database_stamp(path)
    with read_database(path) as db:
        if not {"sessions", "events"} <= tables(db):
            raise ValueError(f"Unsupported ADK database schema: {path}")
        # Large states and tool deltas stay in SQLite. Only project the visible
        # message content here; complete originals are streamed separately.
        sessions = [dict(row) for row in db.execute("SELECT app_name,user_id,id,create_time,update_time FROM sessions")]
        owners = {str(row["user_id"]) for row in sessions}
        if len(owners) > 1 and user_id is None:
            raise ValueError("Multiple session owners found; select exactly one with --user-id.")
        sessions = [row for row in sessions if user_id is None or str(row["user_id"]) == user_id]
        if user_id is not None and not sessions:
            raise ValueError("No sessions found for --user-id")
        if selected and set(selected) - {str(row["id"]) for row in sessions}:
            raise ValueError("Some --session IDs do not exist for the selected owner")
        result, lookup, orders = [], {}, {}
        modern = "event_data" in {row[1] for row in db.execute("PRAGMA table_info(events)")}
        for index, row in enumerate(sessions):
            if selected and str(row["id"]) not in selected:
                continue
            details = db.execute("""SELECT COALESCE(json_extract(state,'$.session_title'),json_extract(state,'$.title')) AS migration_title,
                json_extract(state,'$.session_artifacts') AS migration_artifacts,
                length(CAST(json_quote(state) AS BLOB)) AS migration_state_bytes FROM sessions
                WHERE app_name=? AND user_id=? AND id=?""", (row["app_name"], row["user_id"], row["id"])).fetchone()
            row.update(dict(details))
            if progress:
                progress(f"Reading session {index + 1}/{len(sessions)}: {row['id']}")
            graph_path = home / ".adk" / "agent_graphs" / (str(row["id"]) + ".json")
            # Session IDs are data, never paths supplied by the database.
            graph = str(graph_path) if graph_path.parent == home / ".adk" / "agent_graphs" and graph_path.is_file() and not linked(graph_path) else None
            item = {"key": identity(row["app_name"], row["user_id"], row["id"]),
                "id": str(row["id"]), "title": row.get("migration_title"),
                "created_at": timestamp(row.get("create_time")), "updated_at": timestamp(row.get("update_time")),
                "messages": [], "legacy": {"database": str(path), "database_stamp": stamp,
                    "session": {k: v for k, v in row.items() if not k.startswith("migration_")}, "event_count": 0, "event_rowids": [],
                    "history_bytes_estimate": (row["migration_state_bytes"] or 0) + (graph_path.stat().st_size if graph else 0) + 256,
                    "artifact_paths": sorted(artifact_paths(json_value(row.get("migration_artifacts"), []), "session_artifacts")), "agent_graph_path": graph}}
            result.append(item)
            lookup[row["app_name"], row["user_id"], row["id"]] = item
            orders[item["key"]] = []
        # Upstream may have no index by session. Scan once, then retain rowids
        # for indexed point reads when streaming the complete raw archives.
        fields = "rowid AS migration_rowid,id,app_name,user_id,session_id,timestamp,"
        fields += ("json_extract(event_data,'$.author') AS author,json_extract(event_data,'$.content') AS content,length(CAST(event_data AS BLOB)) AS raw_bytes"
            if modern else "author,content,length(CAST(content AS BLOB)) AS raw_bytes")
        where = " WHERE (app_name,user_id,session_id) IN (VALUES " + ",".join("(?,?,?)" for _ in lookup) + ")" if lookup else " WHERE 0"
        parameters = [value for key in lookup for value in key]
        for event_row in db.execute("SELECT " + fields + " FROM events" + where, parameters):
            event_row = dict(event_row)
            item = lookup.get((event_row["app_name"], event_row["user_id"], event_row["session_id"]))
            if item is None:
                continue
            created = timestamp(event_row.get("timestamp") or item["created_at"])
            order = (datetime.fromisoformat(created).timestamp(), event_row["migration_rowid"])
            orders[item["key"]].append(order)
            item["legacy"]["history_bytes_estimate"] += (event_row["raw_bytes"] or 0) + 256
            content = json_value(event_row.get("content"), {})
            item["legacy"]["artifact_paths"].extend(artifact_paths(content))
            author = event_row["author"]
            # Standalone releases named the user-facing planner MatCreator;
            # newer releases name it thinking_agent. Executor and tool-response
            # events belong to the raw trace, not the conversation transcript.
            user = author == "user" and content.get("role") in {None, "user"}
            agent = author in {"MatCreator", "thinking_agent"} and content.get("role") in {None, "model"}
            if user or agent:
                text = "\n".join(part["text"] for part in content.get("parts", []) if part.get("text") and not part.get("thought"))
                if text.strip():
                    item["messages"].append({"id": str(event_row.get("id") or event_row["migration_rowid"]),
                        "sender_kind": "user" if user else "agent", "content": text, "created_at": created, "_order": order})
        for item in result:
            item["messages"].sort(key=lambda message: message["_order"])
            for message in item["messages"]:
                message.pop("_order")
            item["title"] = str(item["title"] or next((m["content"][:100] for m in item["messages"] if m["sender_kind"] == "user"), item["id"]))[:200]
            item["legacy"]["event_rowids"] = [rowid for _, rowid in sorted(orders[item["key"]])]
            item["legacy"]["event_count"] = len(item["legacy"]["event_rowids"])
            item["legacy"]["artifact_paths"] = sorted(set(item["legacy"]["artifact_paths"]))
    if database_stamp(path) != stamp:
        raise ValueError("Source history changed during discovery; stop its writer and retry")
    return result


def history_archives(source, chunk_bytes=None):
    """Yield bounded JSON parts without retaining the full event history."""
    limit = chunk_bytes or HISTORY_CHUNK_BYTES
    origin = source["legacy"]
    session = origin["session"]
    database = Path(origin["database"])
    if database_stamp(database) != origin["database_stamp"]:
        raise ValueError("Source history changed during migration; stop its writer and retry")
    with read_database(database) as db:
        values = (session["app_name"], session["user_id"], session["id"])
        row = db.execute("SELECT * FROM sessions WHERE app_name=? AND user_id=? AND id=?", values).fetchone()
        if row is None:
            raise ValueError("A source session disappeared during migration")
        graph = Path(origin["agent_graph_path"]).read_bytes() if origin["agent_graph_path"] else b"null"
        prefix = b'{"session":' + json.dumps(dict(row), ensure_ascii=False).encode("utf-8") + b',"agent_graph":' + graph + b',"events":['
        del row, graph
        part, count, total = 1, 0, 0
        chunks, size = [prefix], len(prefix)
        modern = "event_data" in {row[1] for row in db.execute("PRAGMA table_info(events)")}
        for rowid in origin["event_rowids"]:
            row = db.execute("SELECT rowid AS migration_rowid,* FROM events WHERE rowid=? AND app_name=? AND user_id=? AND session_id=?", (rowid, *values)).fetchone()
            if row is None:
                raise ValueError("Source history changed during migration; stop its writer and retry")
            if modern:
                data = row["event_data"].encode("utf-8")
            else:
                event = {**dict(row), "content": json_value(row["content"], {})}
                data = json.dumps(event, ensure_ascii=False).encode("utf-8")
            if size + len(data) > limit and (count or size > limit):
                name = "matcreator-history.json" if part == 1 else f"matcreator-history-{part:04}.json"
                yield name, (*chunks, b'],"event_count":' + str(count).encode() + b'}')
                part += 1
                count = 0
                chunks = [b'{"session_ref":' + json.dumps(source["key"]).encode() + b',"events":[']
                size = len(chunks[0])
            if count:
                chunks.append(b",")
                size += 1
            chunks.append(data)
            size += len(data)
            count += 1
            total += 1
        if total != origin["event_count"] or database_stamp(database) != origin["database_stamp"]:
            raise ValueError("Source history changed during migration; stop its writer and retry")
        name = "matcreator-history.json" if part == 1 else f"matcreator-history-{part:04}.json"
        yield name, (*chunks, b'],"event_count":' + str(count).encode() + b'}')


def packaged_history_archives(archive, source):
    for item in source["legacy"]["archives"]:
        def chunks(path=item["path"]):
            with archive.open(path) as stream:
                while data := stream.read(1024 * 1024):
                    yield data
        yield PurePosixPath(item["path"]).name, chunks()


def read_knowledge(home, graph_paths=()):
    entries, edges = [], []
    paths = list(graph_paths) if graph_paths else [home / ".adk" / name for name in (
        "know_do_graph.db", "skill_graph.db", "memory_graph.db")]
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        if not path.exists():
            if graph_paths:
                raise ValueError(f"Knowledge database is missing: {path}")
            continue
        namespace = identity(str(path))
        with read_database(path) as db:
            found = tables(db)
            if "entries" in found:
                for row in rows(db, "entries"):
                    entry = {**row, "metadata": json_value(row.get("metadata_json"), {}),
                        "tags": json_value(row.get("tags"), []), "aliases": json_value(row.get("aliases"), [])}
                    entries.append({"key": identity(namespace, str(row["id"])), "database": str(path), "raw": entry})
                table = "edges"
            elif "kg_nodes" in found:
                for row in rows(db, "kg_nodes"):
                    kind = row.get("category") or row.get("type")
                    entries.append({"key": identity(namespace, str(row["id"])), "database": str(path), "raw": {
                        **row, "title": row["name"], "content": row.get("description") or "",
                        "entry_type": "memory" if kind == "memory" else "capability",
                        "metadata": {"trust_score": row.get("confidence"), "usage_count": row.get("reference_count") or 0}}})
                table = "kg_edges"
            else:
                raise ValueError(f"Unsupported knowledge database schema: {path}")
            if table in found:
                for row in rows(db, table):
                    edges.append({"source": identity(namespace, str(row["source_id"])),
                        "target": identity(namespace, str(row["target_id"])),
                        "relation": row.get("relation") or row.get("edge_type"), "raw": row})
    for path in sorted((home / ".adk" / "memory").glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        for key, value in (raw.items() if isinstance(raw, dict) else enumerate(raw)):
            entries.append({"key": identity(str(path), str(key)), "database": str(path), "raw": {
                **value, "title": value.get("title") or str(value.get("content", ""))[:100] or "Legacy memory",
                "entry_type": "memory", "metadata": {"custom": {"session_id": path.stem}}}})
    return {"entries": entries, "edges": edges}


def artifact_warnings(sessions, workspace, aliases):
    paths = {path for session in sessions for path in session["legacy"]["artifact_paths"]}
    warnings = []
    for raw in sorted(paths):
        if raw.startswith(("http:", "https:", "oaw:")):
            continue
        mapped = raw
        for alias in aliases:
            if raw == alias or raw.startswith((alias.rstrip("/\\") + "/", alias.rstrip("/\\") + "\\")):
                mapped = str(workspace) + raw[len(alias):].replace("\\", os.sep).replace("/", os.sep)
                break
        path = Path(mapped)
        path = path if path.is_absolute() else workspace / path
        if not path.is_relative_to(workspace):
            warnings.append(f"Artifact reference is outside the selected workspace: {raw}")
        elif not path.exists():
            warnings.append(f"Missing artifact reference: {raw}")
    return warnings


def discover(source, workspace=None, *, user_id=None, selected=(), graph_paths=(), skills_dirs=(), old_workspaces=(), progress=None):
    home = source_home(source)
    workspace = Path(workspace).expanduser().resolve(strict=True) if workspace else home / "workspace"
    if not workspace.is_dir():
        raise ValueError("Workspace is unavailable; specify its actual directory with --workspace.")
    sessions = read_sessions(home, user_id, selected, progress)
    graph = read_knowledge(home, graph_paths)
    memory = workspace / "MEMORY.md"
    if memory.is_file():
        for index, line in enumerate(memory.read_text(encoding="utf-8").splitlines()):
            text = line.strip().lstrip("-* ")
            if len(text) >= 10 and not text.startswith("#"):
                graph["entries"].append({"key": identity(str(memory), str(index), text), "database": str(memory),
                    "raw": {"title": text[:100], "content": text, "entry_type": "memory"}})
    skill_roots = [workspace / "skills", home / "skills", *[Path(p).expanduser().resolve(strict=True) for p in skills_dirs]]
    skills, warnings = [], []
    for root in dict.fromkeys(skill_roots):
        if not root.is_dir():
            continue
        if linked(root):
            warnings.append(f"Skipped linked Skills root; pass its actual directory with --skills-dir: {root}")
            continue
        for folder, dirs, names in os.walk(root, followlinks=False):
            base = Path(folder)
            dirs[:] = [name for name in dirs if name not in EXCLUDED and not linked(base / name)]
            if "SKILL.md" in names and not linked(base / "SKILL.md"):
                key = identity(str(base))
                if not any(skill["id"] == key for skill in skills):
                    skills.append({"id": key, "name": base.name[:120], "source_path": str(base), "source_root": str(root),
                        "path": base.relative_to(workspace).as_posix() if base.is_relative_to(workspace) else "matcreator-imported-skills/" + key})
    owner = user_id or next((str(s["legacy"]["session"]["user_id"]) for s in sessions), None)
    aliases = [str(workspace), *old_workspaces]
    return {"format": FORMAT, "version": VERSION, "source_id": identity(str(home), owner),
        "created_at": timestamp(), "source_home": str(home), "workspace": str(workspace),
        "workspace_aliases": aliases, "sessions": sessions, "knowledge": graph, "skills": skills, "files": [],
        "warnings": [*warnings, *artifact_warnings(sessions, workspace, aliases)]}


def inventory(root, includes=(), excludes=()):
    files, warnings = [], []
    for folder, dirs, names in os.walk(root, followlinks=False):
        base = Path(folder)
        for name in list(dirs):
            path = base / name
            rel = path.relative_to(root).as_posix()
            if linked(path) or name in EXCLUDED or any(fnmatch.fnmatch(rel, pattern) for pattern in excludes):
                dirs.remove(name)
                warnings.append(f"Excluded directory: {rel}")
        for name in sorted(names):
            path = base / name
            rel = path.relative_to(root).as_posix()
            if name in {".env", TRANSFER_RECEIPT} or name.startswith(".env.") or name.endswith(".pyc") or name.casefold().endswith(":zone.identifier") or any(
                    fnmatch.fnmatch(rel, pattern) for pattern in excludes):
                warnings.append(f"Excluded file: {rel}")
                continue
            if includes and not any(fnmatch.fnmatch(rel, pattern) for pattern in includes):
                continue
            if linked(path) or not stat.S_ISREG(path.lstat().st_mode):
                warnings.append(f"Excluded link or special file: {rel}")
                continue
            info = path.stat()
            files.append({"path": rel, "size": info.st_size, "mtime_ns": info.st_mtime_ns})
    return files, warnings


def summary(manifest, packaged=False):
    history = sum(sum(a["size"] for a in s["legacy"]["archives"]) if s["legacy"].get("archives")
        else s["legacy"].get("history_bytes_estimate", 0) for s in manifest["sessions"])
    result = {"source_id": manifest["source_id"], "workspace": manifest["workspace"],
        "sessions": len(manifest["sessions"]), "messages": sum(len(s["messages"]) for s in manifest["sessions"]),
        "history_events": sum(s["legacy"]["event_count"] for s in manifest["sessions"]),
        "history_mode": "copy" if packaged else "reference", "original_history_bytes_estimate": history,
        "knowledge_entries": len(manifest["knowledge"]["entries"]), "knowledge_edges": len(manifest["knowledge"]["edges"]),
        "skills": len(manifest["skills"]), "packed_files": len(manifest["files"]),
        "packed_bytes": sum(f["size"] for f in manifest["files"]), "warnings": manifest["warnings"]}
    if packaged:
        result["package_uncompressed_bytes_estimate"] = history + result["packed_bytes"] + len(json.dumps(manifest, ensure_ascii=False).encode("utf-8"))
    return result


def package_inventory(manifest, includes=(), excludes=()):
    root = Path(manifest["workspace"])
    manifest = {**manifest}
    home = Path(manifest["source_home"])
    if home.is_relative_to(root):
        prefix = home.relative_to(root).as_posix()
        excludes = [*excludes, *[(prefix + "/" if prefix != "." else "") + name for name in ("config.yaml", "config.yml")]]
    manifest["files"], warnings = inventory(root, includes, excludes)
    manifest["warnings"] = [*manifest["warnings"], *warnings]
    for skill in manifest["skills"]:
        source = Path(skill["source_path"])
        if source.is_relative_to(root):
            continue
        items, warnings = inventory(source)
        manifest["warnings"].extend(warnings)
        for item in items:
            virtual = skill["path"] + "/" + item["path"]
            if includes and not any(fnmatch.fnmatch(virtual, pattern) for pattern in includes):
                continue
            if any(fnmatch.fnmatch(virtual, pattern) for pattern in excludes):
                continue
            manifest["files"].append({**item, "path": virtual, "source_path": str(source / item["path"])})
    included = {f["path"] for f in manifest["files"]}
    manifest["skills"] = [skill for skill in manifest["skills"] if skill["path"] + "/SKILL.md" in included]
    return manifest


def validate_file_inventory(files):
    seen = set()
    for item in files:
        relative_path(item["path"])
        key = item["path"].casefold()
        if key in seen:
            raise ValueError(f"Package paths collide on Windows: {item['path']}")
        seen.add(key)


def export_package(manifest, output, *, compress=False, includes=(), excludes=()):
    output = Path(output).expanduser().resolve()
    root = Path(manifest["workspace"])
    if output.is_relative_to(root) or output.exists():
        raise ValueError("Choose a new output file outside the source workspace")
    manifest = package_inventory(manifest, includes, excludes)
    manifest["sessions"] = [{**source, "legacy": {**source["legacy"], "archives": []}} for source in manifest["sessions"]]
    if len(json.dumps(manifest, ensure_ascii=False).encode("utf-8")) > MAX_MANIFEST:
        raise ValueError("Migration metadata exceeds 128 MiB; select fewer sessions")
    ensure_space(output.parent, summary(manifest, packaged=True)["package_uncompressed_bytes_estimate"] if not compress else 1024 * 1024)
    validate_file_inventory(manifest["files"])
    output.parent.mkdir(parents=True, exist_ok=True)
    # Reserve the requested name without ever replacing another export.
    stream = output.open("xb")
    try:
        with stream, zipfile.ZipFile(stream, "w", compression=(
                zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED), allowZip64=True) as archive:
            for item in manifest["files"]:
                path = Path(item["source_path"]) if item.get("source_path") else root.joinpath(*relative_path(item["path"]).parts)
                before = path.stat()
                allowed = [root, *[Path(s["source_path"]) for s in manifest["skills"]]]
                if (linked(path) or not any(path.resolve().is_relative_to(base) for base in allowed)
                        or (before.st_size, before.st_mtime_ns) != (item["size"], item["mtime_ns"])):
                    raise ValueError(f"Source file changed during export: {item['path']}")
                with path.open("rb") as source_stream, archive.open("workspace/" + item["path"], "w", force_zip64=True) as target_stream:
                    while data := source_stream.read(1024 * 1024):
                        ensure_space(output.parent, len(data))
                        target_stream.write(data)
                after = path.stat()
                if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                    raise ValueError(f"Source file changed during export: {item['path']}")
            for source in manifest["sessions"]:
                for name, chunks in history_archives(source):
                    path = "history/" + source["key"] + "/" + name
                    size = 0
                    with archive.open(path, "w", force_zip64=True) as output_stream:
                        for chunk in chunks:
                            ensure_space(output.parent, len(chunk))
                            output_stream.write(chunk)
                            size += len(chunk)
                    source["legacy"]["archives"].append({"path": path, "size": size})
            payload = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
            if len(payload) > MAX_MANIFEST:
                raise ValueError("Migration metadata exceeds 128 MiB; select fewer sessions")
            ensure_space(output.parent, len(payload))
            archive.writestr("manifest.json", payload)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return manifest


def read_package(archive):
    names = set()
    members = {}
    for item in archive.infolist():
        relative_path(item.filename)
        key = item.filename.casefold()
        if key in names or stat.S_ISLNK(item.external_attr >> 16) or item.is_dir():
            raise ValueError("Package contains duplicate paths, links or unexpected directory entries")
        names.add(key)
        members[item.filename] = item
    info = members.get("manifest.json")
    if info is None or info.file_size > MAX_MANIFEST:
        raise ValueError("Missing or oversized migration manifest")
    manifest = json.loads(archive.read(info))
    if manifest.get("format") != FORMAT or manifest.get("version") != VERSION:
        raise ValueError("Unsupported migration package format/version")
    from uuid import UUID
    UUID(manifest["source_id"])
    for item in manifest["skills"]:
        relative_path(item["path"])
    expected = {"manifest.json"}
    for source in manifest["sessions"]:
        UUID(source["key"])
        if not source["legacy"]["archives"]:
            raise ValueError("Package is missing its original session history")
        for item in source["legacy"]["archives"]:
            name = item["path"]
            relative_path(name)
            if (not name.startswith("history/" + source["key"] + "/") or name in expected or name not in members
                    or item["size"] < 0 or members[name].file_size != item["size"]):
                raise ValueError("Package history inventory does not match its contents")
            expected.add(name)
    for item in manifest["files"]:
        relative_path(item["path"])
        if item["path"] == TRANSFER_RECEIPT:
            raise ValueError("Package uses a reserved migration receipt path")
        name = "workspace/" + item["path"]
        if name in expected or name not in members or item["size"] < 0 or members[name].file_size != item["size"]:
            raise ValueError("Package file inventory does not match its contents")
        expected.add(name)
    if expected != set(members):
        raise ValueError("Package contains undeclared files")
    return manifest


def extract_workspace(archive, manifest, destination):
    destination = Path(destination).expanduser().resolve()
    if destination.exists():
        receipt = destination / TRANSFER_RECEIPT
        expected = {"source_id": manifest["source_id"], "files": manifest["files"]}
        if receipt.is_file() and not linked(receipt) and json.loads(receipt.read_text(encoding="utf-8")) == expected:
            if all((destination / item["path"]).is_file() and not linked(destination / item["path"]) for item in manifest["files"]):
                return destination
        raise ValueError("Choose a new --files-to directory; existing files are never overwritten")
    destination.parent.mkdir(parents=True, exist_ok=True)
    needed = sum(item["size"] for item in manifest["files"])
    receipt_data = json.dumps({"source_id": manifest["source_id"], "files": manifest["files"]}).encode("utf-8")
    ensure_space(destination.parent, needed + len(receipt_data))
    stage = Path(tempfile.mkdtemp(prefix=".matcreator-import-", dir=destination.parent))
    try:
        for item in manifest["files"]:
            target = stage.joinpath(*relative_path(item["path"]).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open("workspace/" + item["path"]) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
        (stage / TRANSFER_RECEIPT).write_bytes(receipt_data)
        stage.rename(destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return destination


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--read-source":
        # Private stdlib-only entry point for the native WSL metadata reader.
        print(json.dumps(discover(**json.loads(argv[1])), ensure_ascii=False))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("local", "export"):
        command = commands.add_parser(name)
        command.add_argument("--source", required=True, help="MatCreator home containing .adk (one server user's .matcreator)")
        command.add_argument("--workspace", help="Actual project files directory; default SOURCE/workspace")
        command.add_argument("--old-workspace", action="append", default=[], help="Additional old path prefix, e.g. a container's /root/.matcreator/workspace")
        command.add_argument("--user-id", help="Required when session.db contains multiple owners")
        command.add_argument("--session", action="append", default=[], help="Select history by ID; repeatable. Shared knowledge/files remain included.")
        command.add_argument("--knowledge-db", action="append", default=[], help="Explicit KDG/split-graph database; repeatable")
        command.add_argument("--skills-dir", action="append", default=[], help="Additional collected Skill root; project and user-global skills are detected automatically")
        command.add_argument("--dry-run", action="store_true")
    export = commands.choices["export"]
    export.add_argument("--output", required=True)
    export.add_argument("--compress", action="store_true", help="Deflate; default is a streaming, uncompressed ZIP64 package")
    export.add_argument("--include", action="append", default=[], help="Workspace-relative glob, e.g. project-a/*; repeatable")
    export.add_argument("--exclude", action="append", default=[], help="Workspace-relative glob; repeatable")
    imported = commands.add_parser("import")
    imported.add_argument("--package", required=True)
    imported.add_argument("--files-to", required=True, help="New project directory on this computer")
    imported.add_argument("--dry-run", action="store_true")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--package", required=True)
    for name in ("local", "import"):
        command = commands.choices[name]
        command.add_argument("--data-root", required=True, help="Stopped OAW profile's actual data directory")
        command.add_argument("--group-id", help="Existing MatCreator research group; otherwise create an imported group")
        command.add_argument("--runtime", default="auto", help="Destination Sandbox runtime ID")
        command.add_argument("--read-only", action="store_true", help="Bind project files read-only")
    args = parser.parse_args(argv)
    try:
        if args.command in {"local", "export"}:
            location = wsl_location(args.source) if os.name == "nt" else None
            if location and args.command == "export":
                distribution, native = location
                command = ["python3", "-B", wsl_argument(distribution, str(Path(__file__).resolve())), "export",
                    "--source", native, "--output", wsl_argument(distribution, args.output)]
                if args.workspace:
                    command.extend(["--workspace", wsl_argument(distribution, args.workspace)])
                if args.user_id:
                    command.extend(["--user-id", args.user_id])
                for option, values in (("--session", args.session), ("--old-workspace", args.old_workspace),
                        ("--include", args.include), ("--exclude", args.exclude)):
                    for value in values:
                        command.extend([option, value])
                for option, values in (("--knowledge-db", args.knowledge_db), ("--skills-dir", args.skills_dir)):
                    for value in values:
                        command.extend([option, wsl_argument(distribution, value)])
                command.extend(flag for flag, enabled in (("--compress", args.compress), ("--dry-run", args.dry_run)) if enabled)
                result = wsl_call(distribution, command)
                print(result.stdout, end="")
                print(result.stderr, file=sys.stderr, end="")
                return 0
            reader = discover_wsl if location else discover
            manifest = reader(args.source, args.workspace, user_id=args.user_id, selected=args.session,
                graph_paths=args.knowledge_db, skills_dirs=args.skills_dir, old_workspaces=args.old_workspace,
                progress=lambda value: print(value, file=sys.stderr, flush=True))
            if args.command == "export":
                if args.dry_run:
                    manifest = package_inventory(manifest, args.include, args.exclude)
                    try:
                        validate_file_inventory(manifest["files"])
                    except ValueError as error:
                        manifest["warnings"].append(str(error))
                else:
                    manifest = export_package(manifest, args.output, compress=args.compress, includes=args.include, excludes=args.exclude)
                print(json.dumps(summary(manifest, packaged=True), ensure_ascii=False, indent=2))
                return 0
        else:
            with zipfile.ZipFile(args.package) as archive:
                manifest = read_package(archive)
        print(json.dumps(summary(manifest, packaged=args.command != "local"), ensure_ascii=False, indent=2), flush=True)
        if args.command == "inspect" or args.dry_run:
            return 0
        # Export/inspect work as a single copied script with Python's standard library.
        package = Path(__file__).resolve().parent
        checkout = next((root for root in package.parents if (root / "backend" / "config.py").is_file()), None)
        if checkout is not None:
            sys.path.insert(0, str(checkout))
        sys.path.insert(0, str(package.parent))
        from oaw_matcreator.migration import import_migration
        import asyncio
        with zipfile.ZipFile(args.package) if args.command == "import" else nullcontext() as archive:
            result = asyncio.run(import_migration(manifest, args.data_root,
                workspace=manifest["workspace"] if args.command == "local" else args.files_to,
                group_id=args.group_id, runtime=args.runtime, read_only=args.read_only,
                progress=lambda value: print(value, file=sys.stderr, flush=True),
                copy_history=args.command == "import",
                history_reader=(lambda source: packaged_history_archives(archive, source)) if archive else history_archives,
                external_skills={s["id"]: {"root": s["source_root"], "path": s["source_path"]} for s in manifest["skills"]
                    if not Path(s["source_path"]).is_relative_to(Path(manifest["workspace"]))} if args.command == "local" else None,
                extract=(lambda: extract_workspace(archive, manifest, args.files_to)) if archive else None))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            print("Existing destination OAW settings were preserved; source credentials and model settings were not imported.",
                file=sys.stderr, flush=True)
            if result["destination_settings"]["issues"]:
                print("Data import succeeded. Execution setup requires attention in OAW: "
                    + ", ".join(item["name"] for item in result["destination_settings"]["issues"]), file=sys.stderr, flush=True)
            print(f"Migration complete: {result['totals']['sessions']} sessions, {result['totals']['messages']} messages. "
                f"Open OAW > {result['location']['deck_name']} > {result['location']['group_name']}.", file=sys.stderr, flush=True)
        return 0
    except Exception as error:
        print(f"Migration failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
