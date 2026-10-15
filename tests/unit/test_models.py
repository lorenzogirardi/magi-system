import pytest
from pydantic import ValidationError

from magi_system.models import (
    AgentId,
    ArtifactMeta,
    Phase,
    RepoLocator,
    Requirement,
    RequirementKind,
    stable_id,
)
from magi_system.models.run import LocatorKind, redact_locator


def meta(**kw):
    base = dict(id="r-1", producer=AgentId.PLATFORM, round=0, phase=Phase.INTAKE, input_hash="h")
    base.update(kw)
    return ArtifactMeta(**base)


def test_stable_id_is_deterministic():
    assert stable_id("x", 1, "a") == stable_id("x", 1, "a")
    assert stable_id("x", 1, "a") != stable_id("x", 2, "a")


def test_explicit_requirement_needs_quote_and_derived_needs_trace():
    with pytest.raises(ValidationError):
        Requirement(meta=meta(), kind=RequirementKind.EXPLICIT, text="x")
    with pytest.raises(ValidationError):
        Requirement(meta=meta(), kind=RequirementKind.DERIVED, text="x")
    Requirement(meta=meta(), kind=RequirementKind.DERIVED, text="x", derived_from=("req-1",))


def test_models_are_frozen_and_reject_unknown_fields():
    m = meta()
    with pytest.raises(ValidationError):
        m.round = 3
    with pytest.raises(ValidationError):
        meta(extra=1)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://user:tok@example.org/a/b.git?x=1#f", "https://example.org/a/b.git"),
        ("ssh://u:p@host:2222/repo.git", "ssh://host:2222/repo.git"),
        ("git@host:team/repo.git", "git@host:team/repo.git"),
        ("alice@host:team/repo.git", "***@host:team/repo.git"),
        ("/tmp/repo", "/tmp/repo"),
    ],
)
def test_redact_locator(raw, expected):
    assert redact_locator(raw) == expected


def test_repo_locator_rejects_credentials():
    with pytest.raises(ValidationError):
        RepoLocator(kind=LocatorKind.REMOTE, value="https://u:secret@example.org/r.git")


@pytest.mark.parametrize(
    ("raw", "has"),
    [
        ("https://user:tok@example.org/r.git", True),
        ("https://ghp_token@example.org/r.git", True),
        ("https://example.org/r.git?token=x", True),
        ("ssh://git@example.org/r.git", False),
        ("git@example.org:team/r.git", False),
        ("https://example.org/r.git", False),
        ("/tmp/repo", False),
    ],
)
def test_locator_has_credentials(raw, has):
    from magi_system.models.run import locator_has_credentials

    assert locator_has_credentials(raw) is has
