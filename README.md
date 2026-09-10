# Agentgateway sample with three Streamable HTTP MCP servers

This sample project shows agentgateway federating three independent MCP servers behind one authenticated local endpoint, with Keycloak providing identity and role-based access control:

```text
MCP client / agent
        | user OAuth/JWT
        v
  agentgateway <---- Keycloak UI :8080
    /     |     \   signed service JWTs
   v      v      v
hello   math    time
:8000  :8000   :8000
```

The three upstreams are deliberately small [FastMCP](https://gofastmcp.com/) services. They use MCP Streamable HTTP on `/mcp`; none of them starts or invokes an MCP stdio transport. Python dependencies are pinned in `pyproject.toml` and `uv.lock` and managed with [uv](https://docs.astral.sh/uv/).

## Presentation and demo

The presentation and presenter guide are maintained locally in `deliverables/`, which is excluded from version control. They are not included in a clone of this repository.

## Start the stack

Prerequisites: Docker Desktop or Docker Engine with Docker Compose v2, plus [uv](https://docs.astral.sh/uv/) for the local checks.

```sh
uv sync --frozen
docker compose up --build -d
docker compose ps
uv run --frozen python scripts/check_all.py
```

Compose automatically generates the gateway signing key before starting the MCP servers and gateway. No key files or manual key setup are required in the checkout. `backend-auth-init` exiting with status 0 is expected.

The full check runs local tests, obtains tokens for both imported users, validates sessions and tool permissions, rejects invalid backend credentials, verifies network isolation and content guardrails, and checks traces and dashboard statistics through Grafana and Prometheus. Run it against an otherwise idle stack so usage counts can be reconciled exactly.

The agentgateway playground is available at [http://localhost:3000/ui](http://localhost:3000/ui). The MCP client endpoint is `http://localhost:3000/mcp`.
Keycloak's admin UI is available at [http://keycloak.localhost:8080/admin/](http://keycloak.localhost:8080/admin/), using `admin` / `admin`. The `keycloak.localhost` hostname is intentional: it resolves to the Keycloak container inside Compose and to local loopback in the browser, keeping the JWT issuer consistent. Agentgateway uses the internal `keycloak` service name only for fetching the signing keys.

## Sample identities and permissions

These credentials are intentionally simple and are only for this local sample:

| User | Password | Realm roles | Visible tools |
| --- | --- | --- | --- |
| `alice` | `alice` | `mcp-user` | `hello_world`, `utc_time` |
| `bob` | `bob` | `mcp-user`, `mcp-admin` | `hello_world`, `add_numbers`, `utc_time` |

The imported realm and client are in [`keycloak/mcp-realm.json`](keycloak/mcp-realm.json). `mcp-admin` unlocks the `math` target through the route-level MCP authorization rule.
The realm also intentionally uses HTTP and `sslRequired: NONE` for local development. Do not reuse these credentials or expose this stack outside a trusted machine.

## What is configured

- `mcp-hello` exposes `hello_world`.
- `mcp-math` exposes `add_numbers`.
- `mcp-time` exposes `utc_time`.
- `agentgateway/config.yaml` puts all three targets in one MCP backend, so clients use one endpoint and see a federated tool list.
- Keycloak imports the `mcp` realm, the public PKCE client `agentgateway`, and both sample users on first startup.
- Agentgateway validates Keycloak JWTs and filters the `math` target for users without the `mcp-admin` realm role.
- Each upstream has a `/health` endpoint used by Compose before agentgateway starts.

## Authentication boundaries

The gateway validates Keycloak user tokens and applies Alice/Bob tool permissions. Each MCP server independently authenticates the **gateway service** before processing `/mcp` requests. User tokens are not passed through to the servers.

Agentgateway's `backendAuth.jwtSign` signs a fresh RS256 token for every upstream HTTP request. Tokens expire after 60 seconds and carry a distinct audience (`urn:mcp:hello`, `urn:mcp:math`, or `urn:mcp:time`). FastMCP verifies the gateway's public key, issuer, target audience, required `mcp:invoke` scope and expiration. The small verifier also requires the gateway subject, a bounded expiry and valid time claims. A session ID does not replace authentication. `/health` stays unauthenticated for container health checks.

Each MCP server belongs to its own internal Docker network shared only with the gateway. No MCP server port is published, and sibling MCP servers and observability containers do not share those backend networks. The servers receive only the public verification key. Among the running application services, only the gateway receives the private signing key. Missing or inconsistent keys prevent startup.

The one-shot, networkless `backend-auth-init` service runs `setup_backend_auth.py` to generate a unique RSA-3072 key pair in two Docker volumes: `gateway-private-key` and `gateway-public-key`. Compose waits for it to succeed before starting dependent services, using [startup dependencies](https://docs.docker.com/compose/how-tos/startup-order/). The initializer has write access to both volumes; the gateway and MCP services mount their respective volumes read-only. The private directory has mode `0700`, and the private key has mode `0400`, owned by the gateway's non-root UID 65532. Keys never enter the build context, image layers, source tree, or logs.

Normal restarts and `docker compose down` preserve the key volumes. Rerunning initialization preserves the existing identity, recovers a missing public key from the private key, and refuses mismatched or corrupt keys. Each Docker host/Compose project generates its own keys; separate checkouts on the same host must use distinct Compose project names to avoid sharing volumes. To rotate this sample's identity, stop and remove the gateway, MCP, and initializer containers, remove only the two key volumes, and start Compose again. Clients must reconnect. Avoid `docker compose down -v` for key rotation because it also deletes Grafana and Prometheus data.

The backend check mints short-lived test credentials inside a temporary networkless container; it does not copy the signing key to the host.

User authorization remains centralized: the backend sees the gateway identity, not Alice or Bob. Independent per-user policies at the backend would require delegated user identity and server-side authorization. Backend traffic uses HTTP on the isolated local Docker networks; production deployments should use TLS or mTLS and managed signing keys. The host and Docker daemon remain trusted.

References: [agentgateway signed backend JWTs](https://agentgateway.dev/docs/standalone/latest/documentation/configuration/security/backend-authn/jwt-sign/) and [FastMCP token verification](https://gofastmcp.com/servers/auth/token-verification).

## Demo guardrails

Every `tools/call` goes through agentgateway's native `mcpGuardrails` policy. A small local Python gRPC service checks arguments **before the tool runs** and checks the result **before the client receives it**. This applies to both Alice and Bob, including Bob's admin role. No model, external API or additional credentials are needed.

```text
Client → authenticated gateway → guardrails: check arguments
                             → MCP tool (only if allowed)
                             → guardrails: redact result → client
```

| Example | Result |
| --- | --- |
| Hello: `demo ready` | Allowed |
| Hello: `api_key=demo-only-value` or `password=demo-only-value` | Blocked before Hello runs |
| Hello message longer than 280 characters | Blocked |
| Bob calls Math with `a=1001`, `b=3` | Blocked; each input must be between -1000 and 1000 |
| Hello: `Contact demo@example.com` | Returns `Contact [REDACTED_EMAIL]` inside the greeting |
| Guardrail service unavailable | Tool calls fail closed; discovery remains available |

Run the short demonstration:

```sh
uv run --frozen python scripts/check_guardrails.py
```

It exercises all three tools, four denials, redaction and recovery within the same MCP session, and prints a Grafana trace link. For proof that denied calls never reached an MCP backend, add `--verify-traces`. The optional outage test briefly stops **only** the policy service, then restarts it and verifies recovery:

```sh
uv run --frozen python scripts/check_guardrails.py --verify-traces --check-outage
docker compose logs --tail=30 guardrails
```

The outage check allows for agentgateway's 10-second policy timeout. Policy logs contain only phase, target, decision and rule identifiers. Raw messages, results, email addresses, bearer tokens and session IDs are not logged by this service. Only `Content-Type` is forwarded from client headers to the policy service. The service has no key mounts or published host port; its internal network is shared only with the gateway.

Rules live in [`guardrails/policy.py`](guardrails/policy.py); the hook is configured in [`agentgateway/config.yaml`](agentgateway/config.yaml). Rebuild with `docker compose up --build -d` after changing rules. Regeneration instructions and the pinned v1.5.0 ExtMCP protocol are in [`guardrails/proto/README.md`](guardrails/proto/README.md).

After editing Collector configuration on an already running stack, run `docker compose restart otel-collector` followed by `docker compose restart agentgateway`. This reloads outcome classification and reconnects the gateway exporter if Docker changed the Collector's address. Collector counters restart from zero; existing Prometheus history remains.

These are deliberately narrow, deterministic demo rules. Secret detection recognizes credential assignments, `sk-` tokens and private-key headers; it is not a general prompt-injection or secret detector. Email redaction handles ordinary email patterns in response string values, including text and structured results; the upstream still receives the original email. The policy also rejects invalid argument shapes, numeric strings, booleans and non-finite numbers, limits request parameters to 8 KiB and results to 64 KiB, and allows only the three known target/tool pairs. Adding tools requires updating the policy. This is not a network body-size limit, rate limiter, or general PII filter.

Guardrail denials return JSON-RPC error `-32001`; service failures return `-32603`. They can arrive over HTTP **200**. Grafana identifies these gateway error spans as `guardrail_blocked` or `guardrail_error`, excludes them from successful calls, and shows a **Guardrail rejections** counter and trace link. Successful redactions remain successful calls; use the policy log to see the redaction decision.

References: [MCP guardrail configuration](https://agentgateway.dev/docs/standalone/latest/documentation/mcp/guardrails/setup/) and [protocol behavior](https://agentgateway.dev/docs/standalone/latest/documentation/mcp/guardrails/about/).

## Sharing the sample

`.gitignore` excludes local environment files, key and credential files, Python environments and caches, presentation deliverables, build/telemetry scratch directories, logs, and Office/editor temporary files. `.dockerignore` allows only the Python runtime source, key initializer, and dependency manifests into Docker builds. Keep the README, Compose configuration, realm fixture, dashboard provisioning, tests, and `uv.lock` when sharing the project.

The previous `.secrets/` directory, if present, is ignored and no longer used. Share files selected by Git instead of archiving the whole working folder; `.gitignore` does not filter a Finder/ZIP archive or remove files already tracked in an existing repository. The documented Alice/Bob/admin passwords are deliberately public local-demo fixtures, not private deployment credentials. Do not add real credentials to those fixtures.

## Validation

The live backend check verifies rejection of missing, malformed, expired, cross-server and Keycloak user tokens, verifies a valid service call, and checks network membership and cross-server connectivity. Local HTTP tests additionally cover forged signatures, issuer, subject, scopes, expiry requirements and credential rotation within a session.

Run the whole flow with `uv run --frozen python scripts/check_all.py`, or run individual `scripts/check_*.py` checks as needed.

## Stop the stack

```sh
docker compose down
```

To reset the imported realm after changing [`keycloak/mcp-realm.json`](keycloak/mcp-realm.json), remove the containers and recreate them:

```sh
docker compose down
docker compose up --build -d
```

Create/synchronize the local environment and run the FastMCP tests without Docker:

```sh
uv sync --frozen
uv run --frozen python -m unittest discover -s tests -v
```

## Telemetry in Grafana

[Open Grafana: MCP Gateway · Access & Usage](http://localhost:3001/d/mcp-gateway) to inspect the sample telemetry. The dashboard shows successful calls by tool, authentication failures, hidden/unknown-tool attempts, request rates, p95 latency and success rates. Its **Explore traces**, **Authentication failures**, and **Hidden tool attempts** links open filtered trace searches in Grafana. Select a trace to inspect its request tree and span details.

```sh
docker compose up --build -d
# Required after changing process-level config.tracing settings:
docker compose restart agentgateway
uv run --frozen python scripts/check_telemetry.py
uv run --frozen python scripts/generate_demo_traffic.py
uv run --frozen python scripts/check_dashboard.py
```

The commands above verify the trace pipeline, create a bounded sample workload, and validate the dashboard queries. The workload generator defaults to 12 cycles over approximately 35 seconds: 72 successful Hello calls, 36 Math calls, 12 Time calls, 12 hidden Math attempts, and 24 authentication failures (missing and invalid bearer tokens). The dashboard checker runs one workload cycle plus the guardrail scenarios and reconciles their exact counts, including four content-policy denials. It sums increments from stored samples for each counter series before grouping them, so an observed reset such as `2 → 0 → 2` correctly counts two requests. The assertion stays exact; missing telemetry or concurrent traffic still fails the check. A reset that occurs entirely between stored samples cannot be reconstructed, so keep the stack running during validation.

Agentgateway exports traces over OTLP/gRPC to the Collector. The Collector removes `mcp.session.id` and `jwt.sub`, classifies outcomes, exports traces to Tempo, and creates metrics with the spanmetrics connector. Prometheus scrapes those metrics every five seconds. Grafana queries Prometheus for dashboards and Tempo for traces. The sample client exports its root span over OTLP/HTTP at host loopback port 4318. The gRPC receiver stays internal to Docker.

Counters and rankings show observations **since Collector start**. Time-series charts use the selected time range. Queries count gateway **SERVER spans** once, excluding upstream and guardrail child spans and the demo-client root. Each of the three demo targets has one tool; `demo.tool` maps the known target to its tool name. HTTP 401 identifies an authentication failure, 403 is forbidden, and `400 Unknown tool` has its own category. Alice's hidden Math request is a verified policy-denial example, but arbitrary unknown tools are not proof of an authorization denial. ExtMCP guardrail errors are classified separately even over HTTP 200; other HTTP-successful tool results can still contain application errors that these metrics do not distinguish. p95 values are histogram estimates.

Run `check_telemetry.py` to get fresh Grafana links with a complete parent/child timeline. Both gateway and demo-client spans share the same trace tree. The test reads traces through Grafana and checks receipt of the parent span, trace context and ID redaction. Metric exemplars also link to traces in Grafana.

The local stack uses full sampling. MCP server internals and Keycloak are not instrumented. Tempo keeps traces in container-local storage with a 24-hour retention target; recreating its container removes those traces. Collector counters and queues are volatile and reset when it restarts. Prometheus stores metrics in a named volume with seven-day retention. Grafana runs on host loopback with anonymous Viewer access and Explore enabled; viewers can inspect or temporarily change queries but cannot save dashboard edits. This is reference code, not a durable security audit system.

Configuration lives in `observability/` and `compose.yaml`. Rebuild the provisioned dashboard JSON with `uv run --frozen python observability/grafana/build_dashboard.py` after editing its source. Read the [spanmetrics connector contract](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.135.0/connector/spanmetricsconnector/README.md) and [Grafana trace exploration](https://grafana.com/docs/grafana/latest/datasources/tempo/) for details.
