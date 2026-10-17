#!/usr/bin/env python3
"""Process entry point of the AgentCore image: load the magi-system code bundle, then start the app.

The image holds Python, git, uv and the locked dependencies, but NOT the magi-system code: that is a
zip in S3 (deploy/agentcore/push_code.py), so a code change needs no image build and no Terraform apply.
Every new session is a new microVM and runs this once, so it starts on whatever `code/current.json`
points to; sessions already running keep the code they started with.

    MAGI_CODE_BUCKET        S3 bucket with the bundles (set by Terraform)
    MAGI_CODE_POINTER       pointer object, default code/current.json
    MAGI_CODE_DIR           where bundles are extracted, default /tmp/magi-code
    MAGI_DEPS_HASH_FILE     dependency hash baked into the image, default /opt/magi/deps.sha256

Without MAGI_CODE_BUCKET it just imports magi_system (development, smoke test).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path


class BootstrapError(RuntimeError):
    """The bundle cannot be used; the message says what to do."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _extract(data: bytes, target: Path) -> None:
    """Unzip into `target`, refusing members that would land outside it."""
    import io

    root = target.resolve()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for member in zf.namelist():
            if not (root / member).resolve().is_relative_to(root):
                raise BootstrapError(f"unsafe path in the code bundle: {member!r}")
        zf.extractall(target)


def load_bundle(s3, bucket: str, pointer_key: str, dest_root: Path, deps_hash: str) -> tuple[Path, dict]:
    """Download and unpack the bundle the pointer names; return its directory and the pointer."""
    try:
        pointer = json.loads(s3.get_object(Bucket=bucket, Key=pointer_key)["Body"].read())
        version, key, expected = pointer["version"], pointer["key"], pointer["sha256"]
    except Exception as exc:  # missing object, bad JSON, missing field
        raise BootstrapError(f"cannot read the code pointer s3://{bucket}/{pointer_key}: {type(exc).__name__}: {exc}. "
                             "Run deploy/agentcore/push_code.py") from exc
    if pointer.get("deps_sha256") != deps_hash:
        raise BootstrapError(
            f"the code bundle {version} needs other dependencies than this image (bundle {pointer.get('deps_sha256', '?')[:12]}, "
            f"image {deps_hash[:12]}): rebuild and deploy the image, then push the code again")
    dest = dest_root / version
    if not (dest / ".complete").exists():
        data = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        if _sha256(data) != expected:
            raise BootstrapError(f"code bundle {key} does not match the sha256 in the pointer: refusing to run it")
        tmp = dest_root / f"{version}.tmp-{os.getpid()}"
        _extract(data, tmp)
        (tmp / ".complete").write_text(expected)
        if dest.exists():  # a concurrent start won the race
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
        else:
            tmp.rename(dest)
    return dest, pointer


def main() -> None:
    bucket = os.environ.get("MAGI_CODE_BUCKET")
    if bucket:
        import boto3

        deps_hash = Path(os.environ.get("MAGI_DEPS_HASH_FILE", "/opt/magi/deps.sha256")).read_text().strip()
        dest_root = Path(os.environ.get("MAGI_CODE_DIR", "/tmp/magi-code"))
        dest_root.mkdir(parents=True, exist_ok=True)
        try:
            code_dir, pointer = load_bundle(boto3.client("s3"), bucket, os.environ.get("MAGI_CODE_POINTER", "code/current.json"),
                                            dest_root, deps_hash)
        except BootstrapError as exc:
            print(f"magi bootstrap: {exc}", file=sys.stderr, flush=True)
            raise SystemExit(1) from exc
        sys.path.insert(0, str(code_dir))
        os.environ["MAGI_PLATFORM_REF"] = f"{pointer.get('commit', 'unknown')}@{pointer['version']}"
        print(f"magi bootstrap: code {os.environ['MAGI_PLATFORM_REF']}", flush=True)
    try:
        from magi_system.interfaces.agentcore import main as app_main
    except ImportError as exc:
        raise SystemExit("magi bootstrap: magi_system is not importable (set MAGI_CODE_BUCKET or PYTHONPATH)") from exc
    app_main()


if __name__ == "__main__":
    main()
