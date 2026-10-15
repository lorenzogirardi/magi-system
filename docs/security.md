# Security model

## Enforced in code (not by prompts)

| Boundary | Enforcement |
|---|---|
| MAGI, QA, SRE, Security, verifier are read-only | `Toolbox` exposes no write/delete tools to these roles; `PolicyEngine.check_write` rejects them. |
| Developers write only planned files, tests and docs | Task scope = planned change paths + test scopes + doc scopes, intersected with the target's optional `write_scopes`. |
| Tech Writer writes only doc scopes | `check_write` for `doc.tech_writer`. |
| Protected paths | `.git/**`, `.magi/**`, CI workflow files: never writable; `.git` not even readable. |
| Writes made by commands | After every agent step and every verification, changes outside the allowed scope are reverted and logged (`policy_violation`). |
| Tests cannot be deleted | Deleting a file in test scopes is denied; integrity gate fails if a test file disappears. |
| Policy, prompts, gates | Live in the control repo, never mounted or reachable from the workspace; the control-plane fingerprint is recomputed at the gate. Any change → `POLICY_DENIED`. |
| Path escapes | Paths are normalized; symlinks resolving outside the workspace are rejected. |
| Commands | Executable allowlist + denylist (`git push`, `curl`, `ssh`, `sudo`, package installs, container CLIs ...), timeout, output cap, working dir inside the workspace. |
| Push / merge / deploy | Not implemented. Workspace clones have no remotes. Only `local_candidate` and `patch` publication modes are allowed. |
| Prompt injection | Repository content and tool outputs are wrapped as `<untrusted-data>`; prompts state they are data. Decisions that matter (admissibility, routing, gate) are code, so injected text cannot grant itself anything. |
| Secrets | Redacted (patterns + secret-looking env values) from events, node updates (before checkpointing), reports, tool outputs. Runner environment is scrubbed. |
| Credentials in URLs | Rejected; persisted locators are redacted. |

## Execution isolation

`WorkspaceManager` isolates Git state; it is **not** a sandbox. Command isolation is the
`ExecutionRunner`'s job:

- **ContainerRunner** (default profile): `podman`/`docker run --rm --pull=never
  --network=none --cap-drop=ALL --security-opt=no-new-privileges --read-only`, memory
  and pids limits, tmpfs `/tmp`, **only** the run workspace mounted, no credentials, no
  container socket, image from an allowlisted prefix. Images are never pulled
  automatically: pull the image you need beforehand.
- **LocalRunner** (`local-dev`, `offline-test` profiles only): runs on the host as your
  user with a scrubbed environment and a timeout. **It is not a sandbox**: target code
  can read anything your user can read — including the parent process environment via
  `/proc/<pid>/environ` on Linux, so a scrubbed child environment does **not** protect API
  keys. Use it only for trusted targets and tests, never together with secrets (e.g. in CI).

The container runtime CLI receives only `PATH`, `HOME`, `XDG_RUNTIME_DIR`, `DOCKER_HOST`,
`CONTAINER_HOST`, `DOCKER_CONFIG`; none of these are forwarded into the container. With
docker the container runs as the caller's uid/gid so workspace files stay owned by the
caller. A run with the container runner refuses to start if the image is not present
locally (the platform never pulls).

GitHub Actions trigger rules are in [github-actions.md](github-actions.md).

## Project configuration is not authorization

`.magi/config.yaml` keys that try to change gates, permissions, secrets, network,
publication or policy are rejected and the run ends `POLICY_DENIED`. `not_applicable`
entries are honoured only for gates the platform policy allows. Project commands are
untrusted and go through the same command policy.
