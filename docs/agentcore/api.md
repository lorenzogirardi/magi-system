# AgentCore request flow and API

[Overview](README.md) · [API](api.md) · [Configuration](configuration.md) · [Security](security.md) · [Limits & costs](limits-and-costs.md) · [Deploy](deploy.md) · [Testing](testing.md)

A run takes longer than the 15-minute limit of a synchronous invocation. So `start` and
`resume` return at once and the run continues in a background thread registered as an
AgentCore async task. While it runs, `/ping` reports `HealthyBusy`, which keeps the session
alive past the 15-minute idle timeout.

Use **one session per run**. The `runtimeSessionId` must be at least 33 characters, e.g.
`magi-session-<run_id>-<padding>`. A session refuses a second concurrent run.

| Action | Payload | Returns |
|---|---|---|
| `start` | `repo`, `ref`, `request` (Markdown text), `profile` (default `agentcore`), optional `settings` (run settings object, same schema as `--config`), `project`, `run_id` | `{"run_id", "accepted": true}` |
| `resume` | `run_id` | `{"run_id", "accepted": true}` |
| `status` | `run_id` | `RunService.status`, plus `active_in_session`. Before the run record exists: `STARTING`. If it was rejected before starting: `START_FAILED` with `error`. |
| `report` | `run_id` | `{"report", "candidate_patch", "patch_truncated"}` (patch inline up to 5 MB) |

Any other payload returns `{"error": "invalid_request"}`. A rejected request (profile or
provider not allowed, a run already active, a resume refused) returns
`{"error": "rejected", "detail"}`.

`status` and `report` read DynamoDB and EFS, so any session can answer them. If a session
dies mid-run (unhealthy microVM, 8 h lifetime), call `resume` in a new session. The usual
resume checks apply (prompts, policy and control-plane fingerprint unchanged; workspace and
commits present). A new image version therefore cannot resume an old run, by design.
Automatic resume (EventBridge / Step Functions) is not implemented.
