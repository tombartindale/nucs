"""Object storage for large media (master video, composed and draft video, delivered copies).

Local disk is a scratch area, not the home of the media. When [storage] is configured
(or S3_MEDIA_BUCKET is set), a media file that lives in the bucket is represented in the
tree by a small pointer file beside where it would be:

    edit/master.mp4.s3.json   {"key", "size", "etag", "sha256", "duration", "version_id"}

The pointer answers "is it there, how big, how long" without a network call or ffprobe, and
its mtime changes exactly when the media is replaced, so the mtime-based freshness checks
(fsutil.is_fresh) keep working unchanged. Only commands that need the bytes (compose) call
`workspace`, which downloads into a scratch directory and always removes it afterwards.

With no bucket configured nothing here does anything: `remote()` returns None and every
helper falls back to the plain local file.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from . import fsutil
from .envelope import Fail, sha256_file

POINTER_SUFFIX = ".s3.json"
MEDIA_SUFFIXES = (".mp4",)
PRESIGN_SECONDS = 3600


def pointer_path(p: Path) -> Path:
    return p.with_name(p.name + POINTER_SUFFIX)


def read_pointer(p: Path) -> dict[str, Any] | None:
    data = fsutil.read_json(pointer_path(p))
    return data if isinstance(data, dict) and data.get("key") else None


def is_remote(p: Path) -> bool:
    """True when the media is represented by a pointer rather than a local file."""
    return not p.is_file() and read_pointer(p) is not None


def present(p: Path) -> bool:
    """The media exists, locally or in the bucket."""
    return p.is_file() or read_pointer(p) is not None


def mtime(p: Path) -> float | None:
    """The media's freshness stamp: the file's own mtime, else its pointer's."""
    m = fsutil.mtime(p)
    return m if m is not None else fsutil.mtime(pointer_path(p))


def size(p: Path) -> int | None:
    if p.is_file():
        return p.stat().st_size
    ptr = read_pointer(p)
    return int(ptr["size"]) if ptr and ptr.get("size") is not None else None


def duration(p: Path) -> float | None:
    """The cached duration of remote media, or None (callers probe local files themselves)."""
    ptr = read_pointer(p)
    d = ptr.get("duration") if ptr else None
    return float(d) if d else None


def scratch_base(default: Path) -> Path:
    base = Path(os.environ["BCN_SCRATCH"]) if os.environ.get("BCN_SCRATCH") else default
    base.mkdir(parents=True, exist_ok=True)
    return base


@dataclass
class Store:
    root: Path
    bucket: str
    prefix: str = "media"
    region: str = ""
    _client: Any = None

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                import boto3
                from botocore.config import Config as BotoConfig
            except ImportError as e:
                raise Fail("TOOL_MISSING", "boto3 is not installed, so object storage cannot be used.",
                           hint="pip install 'bcn[s3]'") from e
            kw: dict[str, Any] = {"config": BotoConfig(retries={"max_attempts": 8, "mode": "standard"})}
            if self.region:
                kw["region_name"] = self.region
            if os.environ.get("S3_MEDIA_ENDPOINT"):
                kw["endpoint_url"] = os.environ["S3_MEDIA_ENDPOINT"]
            self._client = boto3.client("s3", **kw)
        return self._client

    # -- naming ---------------------------------------------------------------

    def rel(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            # A scratch path or not-yet-created file: fall back to the lexical relative path.
            return path.relative_to(self.root).as_posix()

    def key(self, path: Path) -> str:
        return f"{self.prefix.strip('/')}/{self.rel(path)}" if self.prefix.strip("/") else self.rel(path)

    # -- operations -----------------------------------------------------------

    def head(self, path: Path) -> dict[str, Any] | None:
        from botocore.exceptions import ClientError
        try:
            return self.client.head_object(Bucket=self.bucket, Key=self.key(path))
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def _write_pointer(self, dest: Path, head: dict[str, Any], sha256: str | None, dur: float | None,
                       pointer_at: Path | None = None) -> dict[str, Any]:
        ptr = {
            "key": self.key(dest),
            "bucket": self.bucket,
            "size": int(head["ContentLength"]),
            "etag": str(head.get("ETag", "")).strip('"'),
            "sha256": sha256,
            "duration": dur,
            "version_id": head.get("VersionId"),
        }
        fsutil.write_json(pointer_path(pointer_at or dest), ptr)
        return ptr

    def put(self, local: Path, dest: Path, dur: float | None = None, sha256: str | None = None,
            delete_local: bool = False, keep_mtime: float | None = None) -> dict[str, Any]:
        """Upload `local` as the media that belongs at `dest`, write dest's pointer, and drop
        any real file at dest (the pointer now stands for it). Verifies size after upload.
        keep_mtime stamps the pointer with the replaced file's own mtime, so migrating a file
        does not make every step that was fresh against it look stale."""
        sha = sha256 or sha256_file(local)
        expected = local.stat().st_size
        self.client.upload_file(str(local), self.bucket, self.key(dest), ExtraArgs={"ContentType": "video/mp4"})
        head = self.head(dest)
        if head is None or int(head["ContentLength"]) != expected:
            raise Fail("TOOL_FAILED", f"Upload of {self.rel(dest)} did not verify (size mismatch).", file=self.rel(dest))
        ptr = self._write_pointer(dest, head, sha, dur)
        if keep_mtime is not None:
            os.utime(pointer_path(dest), (keep_mtime, keep_mtime))
        if dest.is_file() and dest.resolve() != local.resolve():
            dest.unlink()
        if delete_local:
            local.unlink(missing_ok=True)
        return ptr

    def adopt(self, dest: Path, dur: float | None = None, sha256: str | None = None) -> dict[str, Any]:
        """Write a pointer for an object that is already at dest's key (a browser upload)."""
        head = self.head(dest)
        if head is None:
            raise Fail("FS_MISSING", f"{self.key(dest)} is not in the bucket.", file=self.rel(dest))
        if dest.is_file():
            dest.unlink()
        return self._write_pointer(dest, head, sha256, dur)

    def incoming_key(self, token: str, name: str) -> str:
        return f"{self.prefix.strip('/')}/_incoming/{token}/{name}"

    def adopt_incoming(self, incoming_key: str, dest: Path, dur: float | None = None,
                       sha256: str | None = None) -> dict[str, Any]:
        """Move a browser-uploaded object from its staging key to dest's key (server-side),
        write the pointer and delete the staging object. A browser upload never writes the
        final key directly, so a rejected or cancelled upload cannot clobber the master."""
        root = f"{self.prefix.strip('/')}/_incoming/"
        if not incoming_key.startswith(root) or ".." in incoming_key:
            raise Fail("USAGE", "That is not an upload staging key.", file=incoming_key)
        self.client.copy({"Bucket": self.bucket, "Key": incoming_key}, self.bucket, self.key(dest),
                         ExtraArgs={"ContentType": "video/mp4", "MetadataDirective": "REPLACE"})
        head = self.head(dest)
        if head is None:
            raise Fail("TOOL_FAILED", f"Could not move the upload to {self.rel(dest)}.", file=self.rel(dest))
        if dest.is_file():
            dest.unlink()
        ptr = self._write_pointer(dest, head, sha256, dur)
        self.client.delete_object(Bucket=self.bucket, Key=incoming_key)
        return ptr

    def read_range(self, key: str, start: int, end: int) -> bytes:
        r = self.client.get_object(Bucket=self.bucket, Key=key, Range=f"bytes={start}-{end}")
        return r["Body"].read()

    def presign_key(self, key: str, seconds: int = PRESIGN_SECONDS) -> str:
        return self.client.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=seconds)

    def copy(self, src: Path, dst: Path, pointer_at: Path | None = None) -> dict[str, Any]:
        """Server-side copy between two remote media paths: no bytes touch local disk.
        The object lands at dst's key; pointer_at, if given, is where the pointer file is
        written instead of beside dst (package builds its out/ folder in a staging directory
        and swaps it in, so the pointer must be written there but name the final key)."""
        sptr = read_pointer(src)
        if sptr is None:
            raise Fail("FS_MISSING", f"{self.rel(src)} is not in the bucket.", file=self.rel(src))
        self.client.copy({"Bucket": self.bucket, "Key": sptr["key"]}, self.bucket, self.key(dst),
                         ExtraArgs={"ContentType": "video/mp4", "MetadataDirective": "REPLACE"})
        head = self.head(dst)
        if head is None or int(head["ContentLength"]) != int(sptr["size"]):
            raise Fail("TOOL_FAILED", f"Copy to {self.rel(dst)} did not verify.", file=self.rel(dst))
        return self._write_pointer(dst, head, sptr.get("sha256"), sptr.get("duration"), pointer_at=pointer_at)

    def download(self, path: Path, to: Path) -> None:
        ptr = read_pointer(path)
        if ptr is None:
            raise Fail("FS_MISSING", f"{self.rel(path)} is neither on disk nor in the bucket.", file=self.rel(path))
        to.parent.mkdir(parents=True, exist_ok=True)
        extra = {"VersionId": ptr["version_id"]} if ptr.get("version_id") else None
        self.client.download_file(self.bucket, ptr["key"], str(to), ExtraArgs=extra)
        if to.stat().st_size != int(ptr["size"]):
            raise Fail("TOOL_FAILED", f"Download of {self.rel(path)} came back the wrong size.", file=self.rel(path))

    def delete(self, path: Path) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self.key(path))
        pointer_path(path).unlink(missing_ok=True)

    def presign_get(self, path: Path, seconds: int = PRESIGN_SECONDS) -> str:
        ptr = read_pointer(path)
        key = ptr["key"] if ptr else self.key(path)
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key, "ResponseContentType": "video/mp4"},
            ExpiresIn=seconds)


def remote(root: Path) -> Store | None:
    """The configured store for this programme root, or None when media stays on local disk."""
    from .config import load
    cfg = load(root)["storage"]
    bucket = os.environ.get("S3_MEDIA_BUCKET") or cfg.get("bucket") or ""
    if not bucket:
        return None
    prefix = os.environ.get("S3_MEDIA_PREFIX", cfg.get("prefix", "media"))
    region = os.environ.get("S3_MEDIA_REGION") or cfg.get("region") or ""
    return Store(root, bucket, prefix, region)


@contextlib.contextmanager
def workspace(root: Path, paths: list[Path], scratch_in: Path) -> Iterator[dict[Path, Path]]:
    """Yield {media path: a local file holding its bytes}.

    A path that is a real local file maps to itself (storage off, or not yet migrated); a
    path that is only a pointer is downloaded into a scratch directory under `scratch_in`
    (or $BCN_SCRATCH). The scratch directory is removed on exit, success or failure, so a
    job never leaves video behind."""
    store = remote(root)
    mapping: dict[Path, Path] = {}
    tmp: Path | None = None
    try:
        for p in paths:
            if p.is_file():
                mapping[p] = p
            elif store is not None and read_pointer(p) is not None:
                if tmp is None:
                    tmp = Path(tempfile.mkdtemp(prefix=".scratch-", dir=scratch_base(scratch_in)))
                local = tmp / p.name
                store.download(p, local)
                mapping[p] = local
            else:
                raise Fail("FS_MISSING", f"{p.name} is neither on disk nor in the bucket.", file=str(p))
        yield mapping
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def publish(root: Path, local: Path, dest: Path, dur: float | None = None, sha256: str | None = None) -> None:
    """Make `local` the media at `dest`: upload and leave a pointer when storage is on,
    else move it into place. `local` is consumed either way."""
    store = remote(root)
    if store is None:
        if local != dest:
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(local, dest)
        return
    store.put(local, dest, dur=dur, sha256=sha256, delete_local=True)
