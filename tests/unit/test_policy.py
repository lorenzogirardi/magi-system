import pytest

from magi_system.models import AgentId
from magi_system.policy import PolicyEngine, PolicyError


@pytest.fixture
def policy():
    return PolicyEngine("offline-test")


def test_unknown_profile_rejected():
    with pytest.raises(PolicyError):
        PolicyEngine("god-mode")


def test_profile_overrides_platform_but_keeps_gates(policy):
    default = PolicyEngine("default")
    assert default.effective["execution"]["default_runner"] == "container"
    assert policy.effective["execution"]["default_runner"] == "local"
    assert policy.mandatory_gates == default.mandatory_gates


def test_snapshot_hash_changes_with_profile():
    assert PolicyEngine("default").snapshot().sha256 != PolicyEngine("quorum-2").snapshot().sha256
    assert PolicyEngine("default").snapshot().sha256 == PolicyEngine("default").snapshot().sha256


@pytest.mark.parametrize("cmd", ["make test", "python -m pytest -q", "sh ./check.sh", "npm run test", "true"])
def test_allowed_commands(policy, cmd):
    assert policy.check_command(cmd).allowed


@pytest.mark.parametrize(
    "cmd",
    ["curl http://x", "git push origin main", "/usr/bin/make", "sudo make", "rm -rf /", "nc -l 1", "", "npm install x",
     "python -c 'print(1)"],  # unbalanced quote: denied, not a crash
)
def test_denied_commands(policy, cmd):
    assert not policy.check_command(cmd).allowed


@pytest.mark.parametrize("role", [AgentId.MELCHIOR, AgentId.BALTHASAR, AgentId.CASPER, AgentId.QA, AgentId.SECURITY, AgentId.SRE])
def test_read_only_roles_cannot_write(policy, role):
    with pytest.raises(PolicyError):
        policy.check_write(role, "src/a.py", task_scopes=("**",), doc_scopes=("**",))


@pytest.mark.parametrize("path", [".magi/config.yaml", ".git/config", ".github/workflows/ci.yml", "../outside", "/etc/passwd"])
def test_protected_and_escaping_paths(policy, path):
    with pytest.raises(PolicyError):
        policy.check_write(AgentId.DEV1, path, task_scopes=("**",), doc_scopes=("**",))


def test_writer_scopes(policy):
    kw = dict(task_scopes=("src/**",), doc_scopes=("docs/**",), test_scopes=("tests/**",))
    assert policy.check_write(AgentId.DEV1, "src/x.py", **kw) == "src/x.py"
    with pytest.raises(PolicyError):
        policy.check_write(AgentId.DEV1, "docs/x.md", **kw)
    assert policy.check_write(AgentId.TECH_WRITER, "docs/x.md", **kw) == "docs/x.md"
    with pytest.raises(PolicyError):
        policy.check_write(AgentId.TECH_WRITER, "src/x.py", **kw)


def test_cannot_delete_tests(policy):
    with pytest.raises(PolicyError):
        policy.check_write(
            AgentId.DEV1, "tests/test_a.py", task_scopes=("**",), doc_scopes=(), test_scopes=("tests/**",), delete=True
        )
