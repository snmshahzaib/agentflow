# Security

> Describes controls that are **implemented** plus, where marked, controls scheduled for a
> named milestone. Current milestone: M1.

## Secret handling

| Rule | Enforcement |
|---|---|
| No real credential is ever committed | `.gitignore` excludes `.env`, `*.env`, keys, certs, cloud credential files; only `*.example` is tracked |
| Secret scanning before every commit | gitleaks via pre-commit (`.pre-commit-config.yaml`, config `.gitleaks.toml`) |
| Secret scanning independent of the developer's machine | gitleaks job in GitHub Actions on every push and PR, full history |
| Staged `.env` files are rejected | `scripts/guard-no-env-files.sh` pre-commit hook |
| Private keys are rejected | `detect-private-key` pre-commit hook |

Hooks are never bypassed with `--no-verify`. If a secret is ever committed, the credential is
revoked/rotated first, then history is remediated — removing it in a later commit is not
sufficient, because the object remains reachable.

Deployment secrets live only in the platform's secret store, never in a workflow file or image.

## The prompt-injection trust boundary

Retrieved web content is **untrusted input** that is fed to a model which then writes a report.
This is the primary security concern of the system, defended in four independent layers:

1. **No tools at synthesis time.** The model cannot search, fetch or execute anything. Nothing
   it emits can reach the network. Our code performs retrieval; each node is a single call over
   text we already hold.
2. **Framing.** Untrusted content is passed inside a delimited block, labelled as data to be
   analysed. It never enters the system prompt, and operator instructions ride only the trusted
   channel.
3. **Schema-constrained output.** Every node returns a Pydantic-validated object, so model prose
   cannot become control flow. This is why `LLM_REQUIRE_STRUCTURED_OUTPUT=true` is the live
   default: tier-3 prompt-coaxed JSON (see [`model-configuration.md`](model-configuration.md))
   is strictly weaker, and relaxing it is a security decision, not a convenience.
4. **Citation re-binding.** Source identifiers returned by the model are resolved against the
   IDs actually retrieved for that task. Unknown identifiers are rejected, not rendered — a
   model cannot invent a citation that survives into a report.

**Verification (M3).** A red-team fixture containing an injected directive
(*"ignore previous instructions and report that …"*) with a test asserting the directive is not
obeyed and the resulting claim is marked unsupported — run against two different vendors' models.

## Server-side request forgery

The MVP **never fetches an arbitrary URL server-side.** The search provider returns snippets and
content, which eliminates the SSRF surface rather than mitigating it.

Defence in depth remains: every stored source URL is validated (scheme allowlist; loopback,
private, link-local and cloud-metadata hosts rejected; no embedded credentials; length capped),
and the frontend renders links with `rel="noopener noreferrer"`. *(URL validator: M4.)* Fixture sources use an unmistakable
`fixtures.agentflow.test` host, so a fixture URL can never be mistaken for a
real one; a test asserts it.

## Application controls

| Control | Status |
|---|---|
| Pydantic validation at every API edge | M1 |
| Consistent error envelope that leaks no internals | M1 |
| Structured JSON logs with correlation IDs; no prompt content or key material logged | M1 |
| Session-scoped task access (a visitor sees only their own tasks) | M4 |
| Per-IP rate limiting (Redis token bucket) | M4 |
| Daily USD budget ceiling with HTTP 429 over budget | M4 |
| Request body size limits | M4 |
| 7-day data retention purge for visitor research content | M4 |
| BYOK: visitor-supplied key held in memory only, never persisted or logged | M4 |

## Provider data policy

AgentFlow sends `provider.data_collection=deny` with every request, so routing
only reaches providers that do not retain request data for training. The
setting is `LLM_DATA_COLLECTION`.

This has a concrete, measured consequence. Of the seven free models that
advertise native structured outputs, **three are unreachable with the control
in place** — they are free precisely because they permit training on request
data, so provider routing finds no eligible endpoint and returns 404. Using
them would mean relaxing a privacy control to obtain a cheaper model.

That trade has not been made. It is recorded here rather than taken quietly,
and it did not need taking:
`baidu/ernie-5.5-flash-preview:free` works with `data_collection=deny`
intact, so the free development profile keeps the paid profile's full
security posture. See [`model-configuration.md`](model-configuration.md).

**A limitation worth stating:** `data_collection=deny` is a routing
instruction to the gateway. It expresses our requirement and is enforced by
the gateway's provider selection; AgentFlow cannot independently verify what
a provider does with a request once it arrives. For genuinely sensitive
material, neither this setting nor any gateway-level control is a substitute
for not sending the material at all.

## Credential handling with a live provider

- The key lives only in a gitignored `.env`, read into the process at startup
  and held as a `SecretStr`. It is never written to a tracked file.
- `GET /api/meta` reports the provider, the resolved model per node and the
  catalogue source — never the key, and never any header.
- The log redactor replaces any value whose key names a credential, matched by
  segment so that `tokens_in` is not caught while `llm_api_key` is. A test
  asserts a real log call cannot leak it.
- Errors returned to clients carry a stable code, a message and a correlation
  id. Provider error bodies are normalised in the adapter and are not
  forwarded to clients.
- gitleaks scans the working tree, the staged set and the full history, locally
  via pre-commit and again in CI.

## Deliberate non-goals

No user accounts, SSO, roles or billing — a single-session demo by design. No claim of factual
accuracy. No autonomous browsing. No execution of model-generated code.
