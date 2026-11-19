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
and the frontend renders links with `rel="noopener noreferrer"`. Fixture sources use an
unmistakable `fixtures.agentflow.test` host, so a fixture URL can never be mistaken for a
real one; a test asserts it.

Validation **rejects**; it never sanitises. A validator that quietly repairs a hostile URL
turns it into an accepted one, which is how allowlist bypasses happen. Canonicalisation is a
separate function with a separate purpose — deduplication — and the two are deliberately not
the same code path (ADR-014).

## Application controls

**Implemented and tested.** "Tested" means a test asserts the behaviour, not that the
setting exists.

| Control | Where |
|---|---|
| Pydantic validation at every API edge | `app/schemas/`, `tests/test_api.py` |
| Request field length caps (question and feedback, 2,000 characters) | `app/schemas/task.py` |
| Consistent error envelope that leaks no internals | `app/api/errors.py` |
| Structured JSON logs with correlation IDs; no prompt content or key material logged | `app/core/logging.py`, `tests/test_logging.py` |
| Session-scoped task access — a visitor sees only their own tasks | `app/services/task_service.py`, `tests/test_api.py` |
| Per-task token and call budget, enforced before each call | `app/graph/`, `tests/test_live_openrouter.py` |
| Per-task search cap, so a runaway plan cannot drain the search allowance | `app/graph/nodes/research.py` |
| Per-session rate limiting on write endpoints, with `Retry-After` | `app/adapters/ratelimit/`, `tests/test_rate_limit.py` |
| Retention sweep deleting research content past `DATA_RETENTION_DAYS` | `app/workers/jobs.py`, `tests/test_retention.py` |
| URL rejection for unsafe schemes, hosts and credentials | `app/adapters/search/urls.py`, `tests/test_search_urls.py` |

Rate limiting is keyed by session rather than IP, and **fails open** if Redis is unreachable;
read endpoints are deliberately unlimited because polling is how the UI tracks progress
(ADR-017).

### Admission control

Four checks run **before** a task row is written or a job is queued
(`app/services/admission.py`), serialised by a transaction-scoped Postgres advisory lock so
they hold under concurrency:

| Setting | Control | Enforced |
|---|---|---|
| `DEMO_ACCESS_CODE` | Gates **live** research. Without a valid code a task is forced onto the deterministic offline profile — downgraded, not refused, so the public demo still works and cannot spend | yes |
| `DEMO_DAILY_TASK_LIMIT` | Deployment-wide tasks per UTC day | yes |
| `DEMO_DAILY_BUDGET_USD` | Deployment-wide spend per UTC day, read from the ledger's own estimate | yes |
| `DEMO_MAX_TASKS_PER_SESSION` | Tasks one visitor may hold at once; failed and cancelled tasks do not count | yes |

Three properties worth stating:

**The decision is recorded, and the worker obeys the record.** Admitted providers are written
to the task row, and the worker builds its dependencies from that row rather than re-reading
configuration — so a task admitted to the offline profile stays there even with live
credentials loaded in the same process.

**A deployment that forgets to set a code is closed, not open.** With no code configured
nothing matches, so nobody gets live research.

**The code is compared with `hmac.compare_digest`**, and a wrong code is logged and
downgraded rather than echoed back.

Verified under concurrency: 12 simultaneous submissions from one session give exactly 5
accepted and 7 refused against a limit of 5. Before the advisory lock, all 12 were admitted —
a real race that the unit tests missed because they share a database session.

### Deployment blockers

**The application must not be made publicly accessible until all of the following are
reviewed and in place.** This list is the gate, not a wish list:

| # | Blocker | State |
|---|---|---|
| 1 | Access control for live research | **implemented and tested** (M7A) |
| 2 | Daily usage and spend ceilings | **implemented and tested** (M7A) |
| 3 | Per-session task limit | **implemented and tested** (M7A) |
| 4 | Rate limiting under concurrency and Redis failure | **implemented and tested** (M7A) |
| 5 | Secret handling for a hosted environment | **procedure written, never executed** |

Items 1–4 are enforced at admission and verified against a running stack by
`scripts/abuse-check.sh` (21 checks). Item 5 is documented in
[`deployment.md`](deployment.md#secret-handling) but cannot be called done: no host exists, so
nothing has been carried out. The application has still never run anywhere but a local Docker
host.

**The strongest form of item 5 is to need no secret at all.** With `LLM_PROVIDER=stub` and
`SEARCH_PROVIDER=fixture` the public demo runs the complete workflow with no provider
credential anywhere on the host.

## Frontend security

The browser is given model-written text that quotes retrieved web pages, so the report body is
attacker-influenced. It is rendered as hostile input (ADR-019):

| Property | How |
|---|---|
| Embedded HTML is never parsed | `react-markdown` without `rehype-raw`. A `<script>` or `<img onerror>` in a report is displayed as text. |
| Only safe URL schemes become links | An explicit `http`/`https`/`mailto` allowlist (`src/lib/urls.ts`), applied to report links *and* to stored source URLs. A `javascript:` or `data:` href is shown as text and labelled "unsafe link, not opened". |
| Opened pages cannot reach back | `rel="noopener noreferrer"` and `target="_blank"` on every external link. |
| The session cookie is not script-reachable | `HttpOnly`, `SameSite=Lax`, `Secure` in production. Verified from the browser: `document.cookie` does not contain it. |
| No `dangerouslySetInnerHTML` anywhere | React escapes all interpolated values, including hostile source titles and excerpts. |

Each property is pinned by tests — fifteen component tests and four browser tests — because
every one of them is a library default that a future change could silently remove. The browser
tests run the hostile content in a real engine, where a `<script>` would actually execute.

The payloads are injected by the tests rather than committed as fixtures: a corpus of working
exploits in the repository would be a hazard, and the backend's own injection tests already
cover what reaches the model.

### Response headers

| Header | Value |
|---|---|
| `Content-Security-Policy` | `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'` |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |

The policy has no `unsafe-inline` and no `unsafe-eval`, which is possible because the bundle
is self-contained: no CDN, no external fonts, no third-party analytics, and the API is
same-origin through the nginx proxy. A browser test drives the full journey and fails on any
CSP violation, because a policy the application itself violates is a policy someone will
eventually loosen.

**These headers were configured but not served.** nginx *replaces* inherited `add_header`
directives as soon as a block declares one of its own, and the cache-control block that
answers `/` — reached by internal rewrite to `/index.html` — declared one. Every security
header was therefore absent from the pages that mattered while looking correct in the config
file. Found in M5 by reading the headers the browser actually received; the headers are now
repeated in each such block, and a test asserts them from the response rather than from the
configuration.

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

## Dependency advisories

Dependency audits run in CI (`pip-audit --strict` and `pnpm audit`). Findings
are fixed, not suppressed. Exceptions are only ever for advisories with **no
published fix**, each listed here with its reasoning, and CI fails on anything
not on the list so a new advisory surfaces rather than hiding behind a
permanently red job.

**There are currently no accepted exceptions.** `KNOWN_UNFIXABLE` in the CI
audit step is empty, so any advisory at all fails the job.

Resolved during the project, for the record:

| Advisory | Package | Outcome |
|---|---|---|
| [GHSA-fc5q-39xc-vjpf](https://github.com/advisories/GHSA-fc5q-39xc-vjpf) | `picomatch` (high, ReDoS) | **Withdrawn upstream on 2026-11-18.** It was carried as the single accepted exception through M2–M4 because no fixed version existed; it has now been retracted from the advisory database and `pnpm audit` is clean with `picomatch` 4.0.7 still installed. The exception was removed rather than left in place. |
| [PYSEC-2026-1845](https://github.com/advisories) | `pytest` | Fixed by upgrading to `pytest>=9.0.3` (resolved 9.2.0). A development dependency that never ships, but an unlisted advisory fails CI by design. |
| ReDoS | `minimatch` | Pinned forward via a pnpm override. |
| four advisories | LangGraph checkpoint packages, `starlette` | Resolved by version floors recorded in `pyproject.toml` with their advisory IDs. |

The withdrawal is worth recording as a process point: an exception list is only
safe if something keeps checking it. This one was re-checked at every milestone
and removed the day it became unnecessary.

## Deliberate non-goals

No user accounts, SSO, roles or billing — a single-session demo by design. No claim of factual
accuracy. No autonomous browsing. No execution of model-generated code.
