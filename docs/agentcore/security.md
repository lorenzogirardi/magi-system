# Security model on AgentCore

[Overview](README.md) · [API](api.md) · [Configuration](configuration.md) · [Security](security.md) · [Limits & costs](limits-and-costs.md) · [Deploy](deploy.md) · [Testing](testing.md) · [Observability](observability.md)

The rules in [the platform security model](../security.md) all still apply. These things differ:

- **Command sandbox.** `CodeInterpreterRunner` (policy profile `agentcore`, the only runner
  that profile allows) runs every agent and gate command in a Code Interpreter session.
  - The session mounts a **per-run EFS access point rooted at
    `/magi/<run_id>/workspace`**, so it never sees other runs, artifacts or the store.
  - It runs in subnets with **no route out**; its security group allows only NFS to EFS.
  - Its execution role can mount EFS only through an access point other than the runtime's.
    So commands get **no useful AWS credentials** and cannot reach OpenRouter.
  - Each command runs as `env -i PATH=... HOME=/tmp ... timeout <t> <argv>`, with arguments
    shell-quoted.
  - The command policy (allowlist, denylist, working directory inside the workspace) is
    checked **before** anything is sent. A denied command never reaches the sandbox.
  - The runner stops the session and deletes the access point at the end of the run.
- **Orchestrator.** The control plane (code, prompts, policy) is a **code bundle in a private S3
  bucket**, never on EFS, so agents cannot change it: the runtime role has `s3:GetObject` on
  `code/*` only, and the sandbox role has no S3 access. At session start `bootstrap.py` reads
  `code/current.json`, checks the bundle's **sha256** and the hash of the locked dependencies, and
  refuses a bundle that does not match. The base image holds no magi-system code. Whoever can write to
  the bucket can run code in the orchestrator (it holds the OpenRouter key and the runtime role), so
  write access belongs to the person deploying, with their own AWS credentials; the bucket is
  versioned and encrypted, and `push_code.py --rollback` restores an earlier version. The fingerprint
  check still runs at the gate.
- **Profiles and providers.** Callers can request only the profiles in
  `MAGI_ALLOWED_PROFILES`. This prevents asking for `offline-test` (LocalRunner, no sandbox)
  next to the API key. Every agent's provider must be in `MAGI_ALLOWED_PROVIDERS`
  (OpenRouter).
- **Secrets.** The OpenRouter key comes from Secrets Manager at process start, before any
  redactor is built, so it is redacted everywhere like an environment key. It never reaches
  the sandbox.
- **Exit codes.** A response without an exit code, or a failed invocation, is a command
  failure (`exit_code` None), never a PASS.
