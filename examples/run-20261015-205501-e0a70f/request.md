<!--
English translation of the change request given to magi-system for run
run-20261015-205501-e0a70f. The run itself received the original Italian text: explicit
requirements were traced to verbatim Italian quotes. This translation is faithful and
complete; nothing was added or removed.
-->

Act as a team made up of a software architect, backend developer, QA engineer, security engineer and SRE.

GOAL

Implement in the existing FastAPI application a "Dev/SRE Toolbox": a modular feature that lets developers and SREs run technical utilities and get structured results through an API.

This implementation also serves to validate our end-to-end SDLC workflow: analysis, design, development, testing, security review, observability and documentation.

Do not build a new separate service and do not rewrite the existing application.

PHASE 1 — ANALYZE THE REPOSITORY

Before modifying the code:

- Identify the application structure, entrypoint, routers, dependency injection and configuration.
- Identify the Python, FastAPI and Pydantic versions.
- Examine authentication, authorization, logging, error handling and observability.
- Identify the database, migrations and any background execution mechanisms already present.
- Examine tests, linting, type checking, Docker and the CI pipeline.
- Read the repository instructions, including any AGENTS.md and equivalent documents.

Reuse the existing conventions and dependencies. Do not introduce databases, brokers or frameworks without a demonstrable need.

Produce a short implementation plan with assumptions, risks and affected files, then proceed without asking for confirmation on local, reversible decisions.

MVP SCOPE

Implement a catalog of utilities with a common contract and these initial tools:

1. json.validate
   - Accepts JSON text.
   - Checks syntactic validity.
   - Returns the parsed value or an error with its position, when available.
   - Does not accept URLs or file paths.

2. text.diff
   - Accepts two strings.
   - Returns a unified diff.
   - Allows configuring the number of context lines within defined limits.

3. k8s.manifest.inspect
   - Accepts Kubernetes YAML manifests, including multi-document ones.
   - Performs static checks without connecting to a cluster.
   - Handles at least Deployment, StatefulSet and DaemonSet.
   - Reports:
     - containers without requests or limits;
     - missing readinessProbe or livenessProbe;
     - images without a tag or with the latest tag;
     - privileged containers;
     - allowPrivilegeEscalation not explicitly disabled;
     - non-root execution not explicitly required at pod or container level.
   - Distinguishes missing configurations from explicitly risky configurations.
   - Treats init containers separately and avoids applying probe rules that do not apply to them.
   - Do not present these checks as a complete Kubernetes validation or as a security certification.

API

Add, adapting the prefix to the repository's conventions:

GET /api/v1/toolbox/tools
- List of the available tools.
- Identifier, description, version and input schema.

POST /api/v1/toolbox/runs
- Runs one of the available tools.
- For this MVP the checks are local, bounded and synchronous.
- Returns HTTP 200 with the result and a run_id.
- Do not introduce a queue just for these checks.

Example request:

{
  "tool": "k8s.manifest.inspect",
  "input": {
    "manifest": "..."
  }
}

RESULT CONTRACT

Define typed models for request, result and finding.

Example:

{
  "run_id": "uuid",
  "tool": "k8s.manifest.inspect",
  "tool_version": "1.0.0",
  "status": "completed",
  "outcome": "fail",
  "summary": {
    "info": 0,
    "warning": 2,
    "error": 1
  },
  "findings": [
    {
      "code": "K8S_PRIVILEGED_CONTAINER",
      "severity": "error",
      "message": "Container configured as privileged",
      "location": {
        "document_index": 0,
        "resource_kind": "Deployment",
        "resource_name": "example",
        "path": "spec.template.spec.containers[0].securityContext.privileged"
      },
      "suggestion": "Disable privileged unless explicitly needed."
    }
  ],
  "output": {},
  "duration_ms": 12
}

Keep distinct:
- execution status;
- analysis outcome;
- request validation errors;
- unexpected internal errors.

A finding must not be treated as an HTTP error.
Syntactically invalid input must produce a negative analysis result when consistent with the tool's purpose; a request with the wrong structure must follow the app's validation error handling.

Do not add GET /runs/{id} without a real persistence system.
Do not introduce in-memory storage while pretending it is durable.
The run_id initially serves to correlate the response, logs and tracing.

ARCHITECTURE

- Separate router, models, application logic and tool implementations.
- Define a common interface or protocol for the tools.
- Use an explicit registry with unique identifiers.
- Do not use client-controllable dynamic imports, eval, exec or shell.
- Keep the analysis functions independent of FastAPI and easily testable.
- Keep the output deterministic, except for run_id and duration.
- Do not run potentially expensive parsing directly in the event loop: follow the app's appropriate execution model.
- Do not introduce complex plugin systems, LLM agents or distributed infrastructure in this MVP.

SECURITY

- Reuse the existing authentication and authorization.
- Do not create public endpoints that bypass the application's controls.
- Do not execute commands or code supplied by the user.
- Do not make network calls.
- Do not read or write files indicated by the user.
- Use safe YAML parsing, without construction of arbitrary objects.
- Set configurable limits for input size, YAML documents, depth and number of findings.
- Reject inputs exceeding the limits with controlled errors.
- Do not log payloads, manifests, diffs or possible secrets.
- Do not return stack traces or internal details to the client.
- Document what happens when the limits are reached: no silent truncation.

OBSERVABILITY

Reuse the existing stack:
- structured logs with run_id, tool, status and duration;
- tracing through OpenTelemetry if already present;
- metrics for invocations, errors and duration if the app already has an exporter.

Do not install a parallel observability stack.
Do not use run_id, payload or resource names as metric labels.

TESTS

Implement:

1. Unit tests for each tool:
   - valid cases;
   - invalid inputs;
   - empty inputs;
   - limits;
   - deterministic output.

2. API tests:
   - tool catalog;
   - valid execution;
   - unknown tool;
   - malformed request;
   - sanitized internal error;
   - authentication/authorization according to the repository.

3. Kubernetes-specific tests:
   - multi-document YAML;
   - empty documents;
   - unsupported resources;
   - manifests with an unexpected structure;
   - securityContext at pod and container level;
   - init containers;
   - images with a tag, without a tag and referenced by digest;
   - unsafe YAML tags;
   - inputs exceeding the limits.

4. Regression tests to verify that the existing endpoints keep working.

Run the test, lint and type checking commands actually available in the repository.
Do not report as successful any checks that were not run.

DOCUMENTATION

Add:
- a description of the feature;
- working curl examples;
- response examples;
- configuration and limits;
- instructions for adding a new tool;
- limitations of the Kubernetes checks;
- a short architectural note on the choices made and the alternatives ruled out.

ACCEPTANCE CRITERIA

- The existing application starts without regressions.
- The three tools are available through the API.
- Requests and responses are typed and documented in OpenAPI.
- The results have a uniform schema.
- The checks require no network, Kubernetes cluster or external credentials.
- Hostile or out-of-limit inputs produce controlled errors.
- The new and existing tests pass, or any obstacles are documented with evidence.
- No superfluous infrastructure components are introduced.

FINAL REPORT

Conclude with:
1. Features implemented.
2. Architectural choices.
3. Files modified.
4. Commands run and their results.
5. Examples to try the feature.
6. Residual risks and limitations.
7. Any out-of-scope activities.

Implement executable code integrated into the repository, not just a proposal or pseudocode.
