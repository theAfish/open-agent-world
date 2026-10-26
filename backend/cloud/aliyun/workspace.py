"""Operator-owned per-profile Volume assignment, outside writable user data."""
import json
import os
from pathlib import Path
import stat


def load_assignment(data_root, profile_id):
    path = Path(data_root).parent / "aliyun-workspace.json"
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 16384
                or (os.name != "nt" and (info.st_uid != 0 or info.st_mode & 0o022))):
            raise ValueError("Cloud workspace assignment must be a bounded operator-owned regular file")
        value = json.loads(stream.read(16385))
    expected = {"version", "profile_id", "region", "template", "volume", "storage_addresses", "verified"}
    if (not isinstance(value, dict) or set(value) != expected or type(value["version"]) is not int or value["version"] != 1
            or value["profile_id"] != profile_id or value["volume"] != f"oaw-{profile_id}"
            or any(not isinstance(value[key], str) or not value[key].strip() for key in ("region", "template"))
            or not isinstance(value["storage_addresses"], list) or not value["storage_addresses"]
            or any(not isinstance(address, str) for address in value["storage_addresses"])
            or value["verified"] is not True):
        raise ValueError("Cloud workspace assignment must be verified and belong to this profile")
    return value
