#!/usr/bin/env python3
"""Publish the magi-system code to S3 so new AgentCore sessions run it: no image build, no Terraform apply.

    uv run --extra agentcore python deploy/agentcore/push_code.py              # zip src/magi_system, upload, point to it
    uv run --extra agentcore python deploy/agentcore/push_code.py --rollback 3f2a9c1d7e04
    uv run --extra agentcore python deploy/agentcore/push_code.py --dry-run    # build and print, upload nothing

Uploads code/<version>.zip and code/<version>.json (version = first 12 hex of the zip's sha256), then
rewrites the pointer code/current.json, which deploy/agentcore/bootstrap.py reads when a session starts.
The bundle records the hash of the locked dependencies: if it differs from the image's, the bootstrap
refuses to start and tells you to rebuild the image. Needs aws credentials, terraform and uv.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from deps_hash import digest  # noqa: E402  (shared with the Dockerfile)

FIXED_TIME = (2020, 1, 1, 0, 0, 0)  # same sources -> same zip -> same version


def build_zip(src: Path) -> bytes:
    """Zip `src` (the magi_system package) with a reproducible content and layout `magi_system/...`."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(p for p in src.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"):
            info = zipfile.ZipInfo(f"{src.name}/{path.relative_to(src).as_posix()}", FIXED_TIME)
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, path.read_bytes())
    return buf.getvalue()


def deps_hash() -> str:
    """Hash of the locked runtime dependencies; the Dockerfile computes the same on the image."""
    out = subprocess.run(["uv", "export", "--frozen", "--no-dev", "--extra", "agentcore", "--no-hashes", "--no-emit-project",
                          "--no-header", "-q"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return digest(out)


def commit_label() -> str:
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip() or "unknown"
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "src", "pyproject.toml"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return f"{sha}-dirty" if dirty else sha


def bucket_name() -> str:
    return subprocess.run(["terraform", f"-chdir={HERE / 'terraform'}", "output", "-raw", "code_bucket"],
                          capture_output=True, text=True, check=True).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bucket", help="default: terraform output code_bucket")
    ap.add_argument("--rollback", metavar="VERSION", help="point to an already uploaded version instead of building")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.rollback:
        import boto3

        bucket = args.bucket or bucket_name()
        s3 = boto3.client("s3")
        manifest = s3.get_object(Bucket=bucket, Key=f"code/{args.rollback}.json")["Body"].read()
        s3.put_object(Bucket=bucket, Key="code/current.json", Body=manifest, ContentType="application/json")
        print(f"code/current.json -> {args.rollback} (new sessions use it; running ones keep their code)")
        return 0

    data = build_zip(ROOT / "src" / "magi_system")
    sha = hashlib.sha256(data).hexdigest()
    version = sha[:12]
    manifest = {"version": version, "key": f"code/{version}.zip", "sha256": sha, "deps_sha256": deps_hash(),
                "commit": commit_label(), "created": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    print(json.dumps({**manifest, "size_kb": round(len(data) / 1024)}, indent=2))
    if args.dry_run:
        return 0

    import boto3
    from botocore.exceptions import ClientError

    bucket = args.bucket or bucket_name()
    s3 = boto3.client("s3")
    try:
        s3.head_object(Bucket=bucket, Key=manifest["key"])
    except ClientError:
        s3.put_object(Bucket=bucket, Key=manifest["key"], Body=data, ContentType="application/zip")
    body = json.dumps(manifest, indent=2).encode()
    s3.put_object(Bucket=bucket, Key=f"code/{version}.json", Body=body, ContentType="application/json")
    s3.put_object(Bucket=bucket, Key="code/current.json", Body=body, ContentType="application/json")
    print(f"published s3://{bucket}/code/{version}.zip and pointed code/current.json to it; new sessions use it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
