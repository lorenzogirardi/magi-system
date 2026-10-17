"""Code-bundle bootstrap: deploy/agentcore/{bootstrap,push_code,deps_hash}.py (S3 under moto, no AWS)."""

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "agentcore"
sys.path.insert(0, str(DEPLOY))
import bootstrap  # noqa: E402
import deps_hash  # noqa: E402
import push_code  # noqa: E402

BUCKET = "magi-code-test"
DEPS = deps_hash.digest("fastapi==0.1\npydantic==2.0\n")


@pytest.fixture
def s3():
    with moto.mock_aws():
        client = boto3.client("s3", region_name="eu-north-1")
        client.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-north-1"})
        yield client


def package(tmp_path, body="VALUE = 1\n"):
    pkg = tmp_path / "pkg" / "magi_system"
    (pkg / "prompts").mkdir(parents=True)
    (pkg / "__init__.py").write_text(body)
    (pkg / "prompts" / "x.md").write_text("prompt\n")
    (pkg / "__pycache__").mkdir()
    (pkg / "__pycache__" / "x.pyc").write_bytes(b"junk")
    return pkg


def publish(s3, data, *, deps=DEPS, sha=None, pointer_key="code/current.json"):
    sha = sha or hashlib.sha256(data).hexdigest()
    version = sha[:12]
    s3.put_object(Bucket=BUCKET, Key=f"code/{version}.zip", Body=data)
    manifest = {"version": version, "key": f"code/{version}.zip", "sha256": sha, "deps_sha256": deps, "commit": "abc1234"}
    s3.put_object(Bucket=BUCKET, Key=pointer_key, Body=json.dumps(manifest).encode())
    return manifest


def test_bundle_is_reproducible_and_skips_bytecode(tmp_path):
    pkg = package(tmp_path)
    a, b = push_code.build_zip(pkg), push_code.build_zip(pkg)
    assert a == b
    names = zipfile.ZipFile(io.BytesIO(a)).namelist()
    assert "magi_system/__init__.py" in names and "magi_system/prompts/x.md" in names
    assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)
    (pkg / "__init__.py").write_text("VALUE = 2\n")
    assert push_code.build_zip(pkg) != a


def test_load_bundle_extracts_the_package_and_reuses_it(s3, tmp_path):
    data = push_code.build_zip(package(tmp_path))
    manifest = publish(s3, data)
    code_dir, pointer = bootstrap.load_bundle(s3, BUCKET, "code/current.json", tmp_path / "dest", DEPS)
    assert pointer["version"] == manifest["version"]
    assert (code_dir / "magi_system" / "__init__.py").read_text() == "VALUE = 1\n"
    assert (code_dir / "magi_system" / "prompts" / "x.md").exists() and (code_dir / ".complete").exists()
    s3.delete_object(Bucket=BUCKET, Key=manifest["key"])  # a second start must not need the zip again
    again, _ = bootstrap.load_bundle(s3, BUCKET, "code/current.json", tmp_path / "dest", DEPS)
    assert again == code_dir
    assert [p.name for p in (tmp_path / "dest").iterdir()] == [manifest["version"]]  # no leftover temp dir


def test_a_tampered_bundle_is_refused(s3, tmp_path):
    data = push_code.build_zip(package(tmp_path))
    publish(s3, data, sha="0" * 64)  # the pointer promises another hash
    with pytest.raises(bootstrap.BootstrapError, match="does not match the sha256"):
        bootstrap.load_bundle(s3, BUCKET, "code/current.json", tmp_path / "dest", DEPS)
    assert not any((tmp_path / "dest").glob("*/.complete"))


def test_other_dependencies_ask_for_an_image_rebuild(s3, tmp_path):
    publish(s3, push_code.build_zip(package(tmp_path)), deps=deps_hash.digest("fastapi==0.2\n"))
    with pytest.raises(bootstrap.BootstrapError, match="rebuild and deploy the image"):
        bootstrap.load_bundle(s3, BUCKET, "code/current.json", tmp_path / "dest", DEPS)


def test_missing_pointer_says_what_to_run(s3, tmp_path):
    with pytest.raises(bootstrap.BootstrapError, match="push_code.py"):
        bootstrap.load_bundle(s3, BUCKET, "code/current.json", tmp_path / "dest", DEPS)


def test_paths_escaping_the_target_are_refused(s3, tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.py", "boom = 1\n")
    publish(s3, buf.getvalue())
    with pytest.raises(bootstrap.BootstrapError, match="unsafe path"):
        bootstrap.load_bundle(s3, BUCKET, "code/current.json", tmp_path / "dest", DEPS)
    assert not (tmp_path / "evil.py").exists()


def test_dependency_hash_ignores_layout_but_not_versions():
    base = deps_hash.digest("fastapi==0.1\nPydantic_Core==2.0 ; python_version >= '3.9'\n    # via fastapi\n")
    assert base == deps_hash.digest("# header\npydantic-core==2.0\nfastapi[all]==0.1\n")
    assert base != deps_hash.digest("fastapi==0.2\npydantic-core==2.0\n")
    assert base != deps_hash.digest("fastapi==0.1\n")
