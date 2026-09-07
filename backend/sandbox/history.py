"""Bounded host-only command receipts. Portable node state never carries these."""
import json


def key(services, sandbox_id):
    node = services.world.get_card(sandbox_id)
    return f"sandbox_history:{sandbox_id}:{node.created_at.isoformat()}"


def read(services, sandbox_id):
    with services.database.locked() as connection:
        row = connection.execute("SELECT value_json FROM application_settings WHERE key = ?", (key(services, sandbox_id),)).fetchone()
    items = json.loads(row[0]) if row else []
    active = services._sandbox_commands.get(sandbox_id)
    for item in items:
        if item["state"] == "running" and (not active or active["id"] != item["id"]):
            item["state"] = "interrupted"
            item["error"] = "Execution could not be recovered after backend restart; it was not resubmitted."
    return items


def save(services, sandbox_id, item):
    items = [entry for entry in read(services, sandbox_id) if entry["id"] != item["id"]]
    items.append(item)
    with services.database.transaction(immediate=True) as connection:
        connection.execute("INSERT INTO application_settings (key, value_json) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
            (key(services, sandbox_id), json.dumps(items[-20:])))
