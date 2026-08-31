from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import sqlite3
import struct
import threading
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

from backend.errors import (
    ConflictError,
    NotFoundError,
    ResourceValidationError,
    RevisionConflictError,
    UnsafePathError,
)
from backend.persistence.database import Database
from backend.resources.models import (
    ResourceRecord,
    ResourceRevision,
    TextDocument,
    TextEdit,
)
from backend.world.models import CardType, ResourceSummary


_WINDOWS_DEVICES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
_MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_filename(filename: str) -> str:
    if not filename or len(filename) > 255:
        raise ResourceValidationError("filename must contain 1-255 characters")
    if filename in {".", ".."} or filename[-1] in {".", " "}:
        raise UnsafePathError("filename is not a safe managed filename")
    if any(character in filename for character in "\\/\0:<>\"|?*"):
        raise UnsafePathError("filename must not contain a path or reserved characters")
    stem = filename.split(".", 1)[0].upper()
    if stem in _WINDOWS_DEVICES:
        raise UnsafePathError("filename is reserved by Windows")
    return filename


class ManagedResourceStore:
    MAX_TEXT_BYTES = 8 * 1024 * 1024
    MAX_IMAGE_BYTES = 25 * 1024 * 1024

    def __init__(self, database: Database, data_root: str | Path) -> None:
        self.database = database
        self.data_root = Path(data_root).resolve()
        self.assets_root = (self.data_root / "assets").resolve()
        self.text_root = self.assets_root / "text"
        self.image_root = self.assets_root / "images"
        self.text_root.mkdir(parents=True, exist_ok=True)
        self.image_root.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.RLock()

    def create_text(self, card_id: str, filename: str, content: str = "") -> ResourceRecord:
        filename = validate_filename(filename)
        data = content.encode("utf-8")
        if len(data) > self.MAX_TEXT_BYTES:
            raise ResourceValidationError("text resource exceeds the 8 MiB POC limit")
        relative_path = PurePosixPath("assets", "text", f"{uuid4()}.txt").as_posix()
        path = self.resolve_relative_path(relative_path)
        self._atomic_write(path, data)
        now = _now()
        try:
            with self.database.transaction(immediate=True) as connection:
                self._assert_card_type(connection, card_id, CardType.TEXT)
                connection.execute(
                    """
                    INSERT INTO resources (
                        card_id, kind, filename, relative_path, media_type,
                        size_bytes, revision, created_at, updated_at
                    ) VALUES (?, 'text', ?, ?, 'text/plain; charset=utf-8', ?, 1, ?, ?)
                    """,
                    (card_id, filename, relative_path, len(data), now, now),
                )
                connection.execute(
                    """
                    INSERT INTO resource_history (
                        card_id, revision, operation, old_sha256, new_sha256,
                        actor_id, created_at
                    ) VALUES (?, 1, 'create', NULL, ?, NULL, ?)
                    """,
                    (card_id, _sha256(data), now),
                )
        except sqlite3.IntegrityError as exc:
            path.unlink(missing_ok=True)
            raise ConflictError(f"resource for card {card_id!r} already exists") from exc
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return self.get_record(card_id)

    def create_image(
        self,
        card_id: str,
        filename: str,
        media_type: str,
        data_base64: str,
    ) -> ResourceRecord:
        filename = validate_filename(filename)
        if media_type not in _MIME_EXTENSIONS:
            raise ResourceValidationError(
                "image type must be image/png, image/jpeg, image/gif, or image/webp"
            )
        try:
            data = base64.b64decode(data_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ResourceValidationError("image data is not valid base64") from exc
        if not data:
            raise ResourceValidationError("image data must not be empty")
        if len(data) > self.MAX_IMAGE_BYTES:
            raise ResourceValidationError("image resource exceeds the 25 MiB POC limit")
        detected_type, width, height = self._inspect_image(data)
        if detected_type != media_type:
            raise ResourceValidationError(
                f"declared media type {media_type!r} does not match {detected_type!r} data"
            )
        relative_path = PurePosixPath(
            "assets", "images", f"{uuid4()}{_MIME_EXTENSIONS[media_type]}"
        ).as_posix()
        path = self.resolve_relative_path(relative_path)
        self._atomic_write(path, data)
        now = _now()
        try:
            with self.database.transaction(immediate=True) as connection:
                self._assert_card_type(connection, card_id, CardType.IMAGE)
                connection.execute(
                    """
                    INSERT INTO resources (
                        card_id, kind, filename, relative_path, media_type,
                        size_bytes, width, height, revision, created_at, updated_at
                    ) VALUES (?, 'image', ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        card_id,
                        filename,
                        relative_path,
                        media_type,
                        len(data),
                        width,
                        height,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO resource_history (
                        card_id, revision, operation, old_sha256, new_sha256,
                        actor_id, created_at
                    ) VALUES (?, 1, 'create', NULL, ?, NULL, ?)
                    """,
                    (card_id, _sha256(data), now),
                )
        except sqlite3.IntegrityError as exc:
            path.unlink(missing_ok=True)
            raise ConflictError(f"resource for card {card_id!r} already exists") from exc
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return self.get_record(card_id)

    def get_record(self, card_id: str) -> ResourceRecord:
        with self.database.locked() as connection:
            row = connection.execute(
                "SELECT * FROM resources WHERE card_id = ?", (card_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"managed resource for card {card_id!r} does not exist")
        return self._record_from_row(row)

    def maybe_get_record(self, card_id: str) -> ResourceRecord | None:
        with self.database.locked() as connection:
            row = connection.execute(
                "SELECT * FROM resources WHERE card_id = ?", (card_id,)
            ).fetchone()
        return None if row is None else self._record_from_row(row)

    def read_text(self, card_id: str) -> TextDocument:
        record = self.get_record(card_id)
        if record.kind is not CardType.TEXT:
            raise ResourceValidationError(f"card {card_id!r} is not a text resource")
        path = self.resolve_relative_path(record.relative_path, require_exists=True)
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ResourceValidationError("managed text resource is not valid UTF-8") from exc
        return TextDocument(
            card_id=record.card_id,
            filename=record.filename,
            content=content,
            size_bytes=record.size_bytes,
            revision=record.revision,
            updated_at=record.updated_at,
        )

    def read_bytes(self, card_id: str) -> tuple[ResourceRecord, Path]:
        record = self.get_record(card_id)
        path = self.resolve_relative_path(record.relative_path, require_exists=True)
        return record, path

    def replace_text(
        self,
        card_id: str,
        content: str,
        *,
        expected_revision: int | None = None,
        actor_id: str | None = None,
        operation: str = "replace",
    ) -> TextDocument:
        new_data = content.encode("utf-8")
        if len(new_data) > self.MAX_TEXT_BYTES:
            raise ResourceValidationError("text resource exceeds the 8 MiB POC limit")
        with self._write_lock:
            record = self.get_record(card_id)
            if record.kind is not CardType.TEXT:
                raise ResourceValidationError(f"card {card_id!r} is not a text resource")
            if expected_revision is not None and expected_revision != record.revision:
                raise RevisionConflictError(
                    f"expected revision {expected_revision}, current revision is {record.revision}"
                )
            path = self.resolve_relative_path(record.relative_path, require_exists=True)
            old_data = path.read_bytes()
            now = _now()
            new_revision = record.revision + 1
            self._write_managed(path, new_data)
            try:
                with self.database.transaction(immediate=True) as connection:
                    cursor = connection.execute(
                        """
                        UPDATE resources
                        SET size_bytes = ?, revision = ?, updated_at = ?
                        WHERE card_id = ? AND revision = ?
                        """,
                        (len(new_data), new_revision, now, card_id, record.revision),
                    )
                    if cursor.rowcount != 1:
                        raise RevisionConflictError("resource changed during update")
                    connection.execute(
                        """
                        INSERT INTO resource_history (
                            card_id, revision, operation, old_sha256, new_sha256,
                            actor_id, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            card_id,
                            new_revision,
                            operation,
                            _sha256(old_data),
                            _sha256(new_data),
                            actor_id,
                            now,
                        ),
                    )
            except BaseException:
                self._write_managed(path, old_data)
                raise
        return self.read_text(card_id)

    def patch_text(
        self,
        card_id: str,
        edits: list[TextEdit],
        *,
        expected_revision: int | None = None,
        actor_id: str | None = None,
    ) -> TextDocument:
        with self._write_lock:
            document = self.read_text(card_id)
            if expected_revision is not None and expected_revision != document.revision:
                raise RevisionConflictError(
                    f"expected revision {expected_revision}, current revision is {document.revision}"
                )
            ordered = sorted(edits, key=lambda edit: (edit.start, edit.end))
            previous_end = 0
            for edit in ordered:
                if edit.start < previous_end:
                    raise ResourceValidationError("text patch edits must not overlap")
                if edit.end > len(document.content):
                    raise ResourceValidationError(
                        f"text patch range {edit.start}:{edit.end} exceeds content length "
                        f"{len(document.content)}"
                    )
                previous_end = edit.end
            content = document.content
            for edit in reversed(ordered):
                content = content[: edit.start] + edit.text + content[edit.end :]
            return self.replace_text(
                card_id,
                content,
                expected_revision=document.revision,
                actor_id=actor_id,
                operation="patch",
            )

    def list_history(self, card_id: str, *, limit: int = 50) -> list[ResourceRevision]:
        self.get_record(card_id)
        safe_limit = min(max(limit, 1), 200)
        with self.database.locked() as connection:
            rows = connection.execute(
                """
                SELECT revision, operation, old_sha256, new_sha256, actor_id, created_at
                FROM resource_history
                WHERE card_id = ?
                ORDER BY revision DESC
                LIMIT ?
                """,
                (card_id, safe_limit),
            ).fetchall()
        return [ResourceRevision.model_validate(dict(row)) for row in rows]

    def refresh_text_if_changed(
        self, card_id: str, *, actor_id: str
    ) -> TextDocument | None:
        """Record a write performed through a read/write sandbox hard link."""

        with self._write_lock:
            record = self.get_record(card_id)
            if record.kind is not CardType.TEXT:
                return None
            path = self.resolve_relative_path(record.relative_path, require_exists=True)
            data = path.read_bytes()
            if len(data) > self.MAX_TEXT_BYTES:
                raise ResourceValidationError("sandbox text output exceeds the 8 MiB POC limit")
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ResourceValidationError(
                    "sandbox changed a managed text resource to invalid UTF-8"
                ) from exc
            digest = _sha256(data)
            with self.database.transaction(immediate=True) as connection:
                latest = connection.execute(
                    """
                    SELECT new_sha256 FROM resource_history
                    WHERE card_id = ? ORDER BY revision DESC LIMIT 1
                    """,
                    (card_id,),
                ).fetchone()
                if latest is not None and latest["new_sha256"] == digest:
                    return None
                new_revision = record.revision + 1
                now = _now()
                connection.execute(
                    """
                    UPDATE resources
                    SET size_bytes = ?, revision = ?, updated_at = ?
                    WHERE card_id = ?
                    """,
                    (len(data), new_revision, now, card_id),
                )
                connection.execute(
                    """
                    INSERT INTO resource_history (
                        card_id, revision, operation, old_sha256, new_sha256,
                        actor_id, created_at
                    ) VALUES (?, ?, 'sandbox', ?, ?, ?, ?)
                    """,
                    (
                        card_id,
                        new_revision,
                        None if latest is None else latest["new_sha256"],
                        digest,
                        actor_id,
                        now,
                    ),
                )
        return self.read_text(card_id)

    def summary(self, card_id: str, *, include_preview: bool = True) -> ResourceSummary:
        record = self.get_record(card_id)
        preview = None
        if include_preview and record.kind is CardType.TEXT:
            content = self.read_text(card_id).content
            preview = re.sub(r"\s+", " ", content).strip()[:280]
        return ResourceSummary(
            kind=record.kind,
            filename=record.filename,
            media_type=record.media_type,
            size_bytes=record.size_bytes,
            revision=record.revision,
            width=record.width,
            height=record.height,
            preview=preview,
        )

    def remove_file(self, record: ResourceRecord) -> None:
        path = self.resolve_relative_path(record.relative_path)
        path.unlink(missing_ok=True)

    def resolve_relative_path(
        self, relative_path: str, *, require_exists: bool = False
    ) -> Path:
        pure = PurePosixPath(relative_path)
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            raise UnsafePathError("managed resource path must be a normalized relative path")
        candidate = (self.data_root / Path(*pure.parts)).resolve(strict=False)
        try:
            candidate.relative_to(self.assets_root)
        except ValueError as exc:
            raise UnsafePathError("managed resource path escapes the assets directory") from exc
        if require_exists and not candidate.is_file():
            raise NotFoundError("managed resource file is missing")
        # Resolving an existing symlink above detects links that leave assets.
        if candidate.exists():
            resolved = candidate.resolve(strict=True)
            try:
                resolved.relative_to(self.assets_root)
            except ValueError as exc:
                raise UnsafePathError("managed resource symlink escapes the assets directory") from exc
            candidate = resolved
        return candidate

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / f".{path.name}.{uuid4()}.tmp"
        try:
            with temporary.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @classmethod
    def _write_managed(cls, path: Path, data: bytes) -> None:
        # Sandbox attachments are hard links. Replacing the path would sever
        # synchronization, so preserve the inode whenever another link exists.
        if path.exists() and path.stat().st_nlink > 1:
            with path.open("r+b") as stream:
                stream.seek(0)
                stream.write(data)
                stream.truncate()
                stream.flush()
                os.fsync(stream.fileno())
            return
        cls._atomic_write(path, data)

    @staticmethod
    def _assert_card_type(connection: sqlite3.Connection, card_id: str, expected: CardType) -> None:
        row = connection.execute("SELECT type FROM cards WHERE id = ?", (card_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"card {card_id!r} does not exist")
        if CardType(row["type"]) is not expected:
            raise ResourceValidationError(
                f"card {card_id!r} is {row['type']!r}, expected {expected.value!r}"
            )

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> ResourceRecord:
        return ResourceRecord(
            card_id=row["card_id"],
            kind=CardType(row["kind"]),
            filename=row["filename"],
            relative_path=row["relative_path"],
            media_type=row["media_type"],
            size_bytes=row["size_bytes"],
            width=row["width"],
            height=row["height"],
            revision=row["revision"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _inspect_image(data: bytes) -> tuple[str, int, int]:
        if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
            width, height = struct.unpack(">II", data[16:24])
            if width and height:
                return "image/png", width, height
        if data[:6] in {b"GIF87a", b"GIF89a"} and len(data) >= 10:
            width, height = struct.unpack("<HH", data[6:10])
            if width and height:
                return "image/gif", width, height
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP" and len(data) >= 30:
            if data[12:16] == b"VP8X":
                width = 1 + int.from_bytes(data[24:27], "little")
                height = 1 + int.from_bytes(data[27:30], "little")
                return "image/webp", width, height
            if data[12:16] == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
                width = int.from_bytes(data[26:28], "little") & 0x3FFF
                height = int.from_bytes(data[28:30], "little") & 0x3FFF
                if width and height:
                    return "image/webp", width, height
            if data[12:16] == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
                width = 1 + data[21] + ((data[22] & 0x3F) << 8)
                height = 1 + (data[22] >> 6) + (data[23] << 2) + ((data[24] & 0x0F) << 10)
                return "image/webp", width, height
        if data.startswith(b"\xff\xd8"):
            index = 2
            while index + 9 < len(data):
                if data[index] != 0xFF:
                    index += 1
                    continue
                marker = data[index + 1]
                index += 2
                if marker in {0xD8, 0xD9}:
                    continue
                if index + 2 > len(data):
                    break
                segment_length = int.from_bytes(data[index : index + 2], "big")
                if segment_length < 2 or index + segment_length > len(data):
                    break
                if marker in {
                    0xC0,
                    0xC1,
                    0xC2,
                    0xC3,
                    0xC5,
                    0xC6,
                    0xC7,
                    0xC9,
                    0xCA,
                    0xCB,
                    0xCD,
                    0xCE,
                    0xCF,
                }:
                    height = int.from_bytes(data[index + 3 : index + 5], "big")
                    width = int.from_bytes(data[index + 5 : index + 7], "big")
                    if width and height:
                        return "image/jpeg", width, height
                index += segment_length
        raise ResourceValidationError("image data is corrupt or uses an unsupported encoding")
