"""Local content-addressed artifact storage.

A payload becomes addressable only after its verified bytes are atomically replaced at
its final CAS path. Registering returned metadata in SQLite is deliberately left to
the calling application transaction.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final

CAS_URI_PREFIX: Final[str] = "cas://sha256/"


class ArtifactStoreError(OSError):
    """Raised for invalid or inconsistent local CAS contents."""


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    """Verified metadata for a payload stored in the local CAS."""

    cas_uri: str
    content_hash: str
    byte_size: int
    media_type: str


class LocalArtifactStore:
    """A local SHA-256 content-addressed store with same-filesystem atomic writes."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put_bytes(self, payload: bytes, media_type: str) -> StoredArtifact:
        """Persist ``payload`` atomically and return verified metadata.

        The metadata is not authoritative until its caller records it in the matching
        SQLite transaction. A pre-existing digest must have the same byte size and
        media type; this prevents silently reinterpreting an existing artifact.
        """
        if not media_type:
            raise ValueError("media_type must not be empty")

        digest = hashlib.sha256(payload).hexdigest()
        content_hash = f"sha256:{digest}"
        payload_path = self._payload_path(digest)
        manifest_path = payload_path.with_name("manifest.json")
        payload_path.parent.mkdir(parents=True, exist_ok=True)

        if payload_path.exists():
            self._verify_payload(payload_path, digest, len(payload))
            self._verify_or_create_manifest(manifest_path, content_hash, len(payload), media_type)
            return StoredArtifact(
                cas_uri=self._uri(digest),
                content_hash=content_hash,
                byte_size=len(payload),
                media_type=media_type,
            )

        temporary_path = payload_path.with_name(f".payload-{uuid.uuid4().hex}.tmp")
        try:
            self._write_and_sync(temporary_path, payload)
            self._verify_payload(temporary_path, digest, len(payload))
            os.replace(temporary_path, payload_path)
            self._sync_directory(payload_path.parent)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

        self._verify_payload(payload_path, digest, len(payload))
        self._verify_or_create_manifest(manifest_path, content_hash, len(payload), media_type)
        return StoredArtifact(
            cas_uri=self._uri(digest),
            content_hash=content_hash,
            byte_size=len(payload),
            media_type=media_type,
        )

    def read_bytes(self, reference: str) -> bytes:
        """Read and integrity-check a payload and its complete CAS metadata."""
        metadata = self.metadata(reference)
        digest = self._digest_from_reference(reference)
        payload_path = self._payload_path(digest)
        try:
            payload = payload_path.read_bytes()
        except FileNotFoundError as error:
            raise ArtifactStoreError(f"artifact payload is missing: {reference}") from error
        if len(payload) != metadata.byte_size:
            raise ArtifactStoreError(f"artifact byte size is inconsistent: {reference}")
        self._verify_payload_bytes(payload, digest)
        return payload

    def metadata(self, reference: str) -> StoredArtifact:
        """Read and validate metadata for an already stored artifact."""
        digest = self._digest_from_reference(reference)
        payload_path = self._payload_path(digest)
        manifest_path = payload_path.with_name("manifest.json")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ArtifactStoreError(f"artifact manifest is missing: {reference}") from error
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ArtifactStoreError(f"artifact manifest is invalid: {reference}") from error
        if not isinstance(manifest, dict):
            raise ArtifactStoreError(f"artifact manifest is invalid: {reference}")

        content_hash = manifest.get("content_hash")
        byte_size = manifest.get("byte_size")
        media_type = manifest.get("media_type")
        expected_manifest = self._manifest(
            content_hash if isinstance(content_hash, str) else "",
            byte_size if isinstance(byte_size, int) and not isinstance(byte_size, bool) else -1,
            media_type if isinstance(media_type, str) else "",
        )
        if (
            content_hash != f"sha256:{digest}"
            or not isinstance(byte_size, int)
            or isinstance(byte_size, bool)
            or byte_size < 0
            or not isinstance(media_type, str)
            or not media_type
            or manifest != expected_manifest
        ):
            raise ArtifactStoreError(f"artifact manifest is inconsistent: {reference}")
        self._verify_payload(payload_path, digest, byte_size)
        return StoredArtifact(
            cas_uri=self._uri(digest),
            content_hash=content_hash,
            byte_size=byte_size,
            media_type=media_type,
        )

    def _payload_path(self, digest: str) -> Path:
        return self.root / "sha256" / digest[:2] / digest[2:] / "payload"

    @staticmethod
    def _uri(digest: str) -> str:
        return f"{CAS_URI_PREFIX}{digest}"

    @staticmethod
    def _write_and_sync(path: Path, payload: bytes) -> None:
        with path.open("xb") as artifact_file:
            artifact_file.write(payload)
            artifact_file.flush()
            os.fsync(artifact_file.fileno())

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _verify_or_create_manifest(
        self, manifest_path: Path, content_hash: str, byte_size: int, media_type: str
    ) -> None:
        expected_manifest = self._manifest(content_hash, byte_size, media_type)
        if manifest_path.exists():
            self._verify_manifest(manifest_path, expected_manifest)
            return

        temporary_path = manifest_path.with_name(f".manifest-{uuid.uuid4().hex}.tmp")
        try:
            encoded = json.dumps(
                expected_manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            self._write_and_sync(temporary_path, encoded)
            try:
                os.link(temporary_path, manifest_path)
            except FileExistsError:
                self._verify_manifest(manifest_path, expected_manifest)
            else:
                self._sync_directory(manifest_path.parent)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    @staticmethod
    def _verify_manifest(manifest_path: Path, expected_manifest: dict[str, object]) -> None:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ArtifactStoreError(f"artifact manifest is missing: {manifest_path}") from error
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ArtifactStoreError(f"artifact manifest is invalid: {manifest_path}") from error
        if manifest != expected_manifest:
            raise ArtifactStoreError(
                f"artifact metadata conflicts with existing payload: {expected_manifest['content_hash']}"
            )

    @staticmethod
    def _manifest(content_hash: str, byte_size: int, media_type: str) -> dict[str, object]:
        return {
            "byte_size": byte_size,
            "content_hash": content_hash,
            "media_type": media_type,
        }

    @staticmethod
    def _verify_payload(path: Path, digest: str, byte_size: int) -> None:
        try:
            payload = path.read_bytes()
        except FileNotFoundError as error:
            raise ArtifactStoreError(f"artifact payload is missing: {path}") from error
        if len(payload) != byte_size:
            raise ArtifactStoreError(f"artifact byte size is inconsistent: {path}")
        LocalArtifactStore._verify_payload_bytes(payload, digest)

    @staticmethod
    def _verify_payload_bytes(payload: bytes, digest: str) -> None:
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ArtifactStoreError("artifact content hash is inconsistent")

    @staticmethod
    def _digest_from_reference(reference: str) -> str:
        if reference.startswith(CAS_URI_PREFIX):
            digest = reference.removeprefix(CAS_URI_PREFIX)
        elif reference.startswith("sha256:"):
            digest = reference.removeprefix("sha256:")
        else:
            raise ValueError("artifact reference must be a CAS URI or SHA-256 display digest")
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("artifact reference must contain a lowercase SHA-256 digest")
        return digest
