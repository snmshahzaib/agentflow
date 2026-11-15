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
and the frontend renders links with `rel="noopener noreferrer"`. *(URL validator: M4.)*

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

## Deliberate non-goals

No user accounts, SSO, roles or billing — a single-session demo by design. No claim of factual
accuracy. No autonomous browsing. No execution of model-generated code.
