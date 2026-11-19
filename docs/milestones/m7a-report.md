# M7A report — secure deployment preparation and hosting assessment

**Status: complete, awaiting review. Nothing has been deployed, provisioned or made public.**

M7A had two jobs: find out whether this architecture can actually be hosted for nothing, and
close the five gates that have been open since M4. The hosting answer is a qualified yes with
one unavoidable condition. Four of the five gates are now implemented and tested; the fifth
cannot honestly be called done until a host exists.

<a id="hosting"></a>

## 1. Hosting assessment

### What the architecture actually needs

| Requirement | Why it constrains the choice |
|---|---|
| An always-on **background worker** | A research run takes minutes. The API never calls a model. |
| **Two cron schedules** | Orphan sweep every 2 minutes, retention daily. |
| **Persistent PostgreSQL** | Domain rows *and* LangGraph checkpoints. Losing it loses the durable approval pause. |
| **Redis that tolerates a polling queue** | arq polls continuously; see the measurement below. |
| A **static frontend** | Trivial anywhere. |
| **Persistent disk** | Backups, and Postgres data surviving a restart. |

### Free tiers as they stand in November 2026

| Platform | Worker | Cron | Postgres | Redis | Card | Verdict |
|---|---|---|---|---|---|---|
| **Render** | ✗ no free instance type | billed, min ~$1/mo per job | free, **expires after 30 days** | 25 MB key-value | not required | **unsuitable** — the worker alone rules it out |
| **Fly.io** | ✓ | ✓ | paid | via Upstash | **$1 trial for 7 days, then card required** | **not free** |
| **Railway** | ✓ | ✓ | ✓ | ✓ | **no free tier since Oct 2024** | **not free** |
| **Neon** | — | — | **always free**, 1 GB/project, scales to zero after 5 min | — | not required | good for Postgres |
| **Upstash Redis** | — | — | — | always free, 256 MB, **500k commands/month** | not required | **insufficient — measured below** |
| **Supabase** | — | — | free, but **pauses after 7 days idle** | — | not required | unsuitable unattended |
| **Oracle Cloud Always Free** | ✓ | ✓ | self-hosted | self-hosted | **required for identity verification** | **viable, with conditions** |

Always-free versus not, stated plainly as asked:

- **Always free, no card:** Neon (Postgres), Upstash (Redis), Render's web/static/Postgres
  instances (but the Postgres expires at 30 days).
- **Time-limited trial:** Fly.io — $1 of credit, 7 days.
- **No free tier at all:** Railway.
- **Always free but card required:** Oracle Cloud. The card is for identity verification and
  is not charged unless the account is upgraded; prepaid and virtual cards are refused.

### The measurement that decided it

Serverless Redis free tiers are priced per command, and arq polls. Rather than estimate, I
reset the command counters on an **idle** stack and sampled for 60 seconds:

```
cmdstat_zrangebyscore: calls=120      <- arq's 0.5s poll
cmdstat_ping:          calls=14
cmdstat_psetex:        calls=4
...                    148 commands/minute, with zero traffic
```

Extrapolated: **6,393,600 commands/month**, against Upstash's free allowance of 500,000. That
is 12.8× over — the free month is spent in **2.3 days by an idle deployment**. Slowing arq's
poll does not rescue it either: 5 s still exceeds the allowance, and only 15 s fits, which
would add up to 15 seconds of pickup latency plus the cost of the 2-minute orphan sweep.

**Conclusion: a polling queue is incompatible with a per-command free tier.** Redis must be
self-hosted, which means a host that can run a long-lived process — which is the same
constraint the worker already imposes.

### Recommendation

**A single always-free VM running the existing Compose stack**, with Caddy for TLS. Oracle
Cloud Always Free (2 OCPU / 12 GB ARM as of June 2026, halved from the widely-quoted 4/24) is
the only option found that supplies every requirement at zero cost.

What this preserves: the deployed topology is *identical* to the developed and tested one.
API, worker, Postgres, Redis, crons, one image, two process types.

What it costs honestly:

- **A credit card is required** for Oracle's identity check. It is not charged. If that is
  unacceptable, there is no zero-cost option that runs this architecture unmodified.
- **Idle reclaim.** Oracle may reclaim an instance whose 7-day P95 CPU, network *and* memory
  all sit below 20%. A low-traffic demo is a plausible candidate. Mitigation is to keep the
  instance genuinely busy or accept the risk.
- **One host is a single point of failure**, and TLS renewal, backups and OS patching are
  ours.
- **No managed backups.** A nightly `pg_dump` to the host is written down; copying it off-host
  is a manual step.

### The alternative, if no card is acceptable

Render (web service) + Neon (Postgres) + the worker collapsed into the API process, with an
external pinger for the crons. This is a **degraded topology**, not a configuration change: it
abandons the two-process model, accepts that a free web service spins down when idle — which
is fatal for a worker mid-run — and needs a third-party scheduler. It is recorded as an option
because it exists, not because it is recommended.

### Unavoidable costs

**None in money**, on the recommended path. The unavoidable costs are a credit card on file,
the idle-reclaim risk, and the operational work of running a VM.

## 2. Security controls

### Gate status

| # | Gate | State | Evidence |
|---|---|---|---|
| 1 | Access control for live research | **implemented, tested** | 5 unit tests + the worker-obedience test |
| 2 | Daily usage and spend ceilings | **implemented, tested** | 5 unit tests incl. concurrency |
| 3 | Per-session task limits | **implemented, tested** | 4 unit tests + live concurrency |
| 4 | Rate limiting under concurrency and Redis failure | **implemented, tested** | 21-check live abuse run |
| 5 | Hosted secret handling | **procedure written, never executed** | `docs/deployment.md` |

Gate 5 is deliberately not marked complete. The procedure is written and the properties it
relies on are tested — no credential in any image, redaction in logs, no key or model
selectable by a visitor — but nothing has been carried out, because there is no host.

### How the gates work

**Live research is gated, by downgrade rather than refusal.** A deployment configured with
live providers still serves every anonymous visitor the deterministic offline profile; a task
reaches a real provider only when the request carries a valid `X-Access-Code`. The public demo
keeps working and simply cannot spend anything.

**The decision is recorded and obeyed.** Admitted providers are written to the task row, and
the worker builds its dependencies from that row rather than re-reading configuration. A task
admitted to the offline profile stays there even with live credentials loaded in the same
process — the difference between a control and a suggestion.

**A forgotten code fails closed.** With no code configured, nothing matches.

**Ceilings are deployment-wide and per UTC day**, counted with an upsert rather than
read-modify-write, and spend is taken from the ledger's own estimate rather than an intention.

**The per-session limit counts only occupying tasks.** Failed and cancelled tasks do not
count: our outage must not become the visitor's quota problem.

### Two real exposures found and fixed

**The per-session limit did not hold under concurrency.** Twelve simultaneous submissions from
one session were all admitted against a limit of five. Every admission check is
read-then-write, and concurrent requests each read the same pre-insert state. The unit tests
missed it because they share a database session, which serialises them for free.

Fixed with a transaction-scoped Postgres advisory lock, released on commit or rollback with no
cleanup path to forget. Re-measured: 12 concurrent submissions now give exactly 5 × 202 and
7 × 429; 40 give 5 and 35. Two regression tests reproduce it on **separate connections**,
which is the only way the race appears.

**The production overlay left the API publicly bound.** Compose *merges* port lists rather
than replacing them, so an overlay entry is appended and the base file's binding survives.
`docker compose config` showed `api 0.0.0.0:8000->8000, 127.0.0.1:8000->8000` — the API
reachable without TLS, bypassing Caddy. The bind address now comes through the port variables,
which compose substitutes whole, and the rendered configuration is checked: nothing is
publicly bound except Caddy.

### A failure mode fixed on the way

A submission while Redis was down raised an unhandled `ConnectionError` — a 500 carrying the
broker's own message, which names hosts and ports. Queue operations now raise a domain error
mapped to **503**, the unit of work rolls back so no orphan task row survives, and nothing
about the broker reaches the client.

### Other requirements

| Requirement | State |
|---|---|
| No anonymous unlimited access to live AI or search | enforced by gate 1 |
| No user-selected paid models or credentials | the request schema forbids unknown fields; tested against four smuggling attempts |
| `LLM_FREE_MODELS_ONLY=true` | unchanged, in the production template |
| `LLM_DATA_COLLECTION=deny` | unchanged, in the production template |
| Strict structured-output validation | unchanged |
| No cross-session access | 404 (never 403) on all eight task routes, tested live and in unit tests |
| No sensitive data in logs or errors | redaction tested; a configured key does not appear in any error response |
| CORS, CSP, proxy headers, HTTPS | reviewed; see below |
| Database and Redis not publicly reachable | verified from the rendered compose config |
| Retention | implemented since M4, scheduled daily |

**CORS** allows exactly the deployment origin and the headers the client sends, now including
`X-Access-Code`. **CSP** is unchanged from M5: no `unsafe-inline`, no `unsafe-eval`, with a
browser test that fails on any violation. **Proxy headers** — Caddy sets
`X-Forwarded-Proto/For/Real-IP`; the application does not currently trust them for anything,
which is noted below as a limitation rather than claimed as a feature. **HTTPS** is Caddy with
automatic Let's Encrypt and HSTS.

## 3. Test results

| Suite | Result |
|---|---|
| Backend | **452 passed**, 20 skipped (was 419) |
| New admission tests | 21 |
| New abuse tests | 14 |
| Live abuse run (`scripts/abuse-check.sh`) | **21 checks passed, 0 failed** |
| Frontend components | 58 passed |
| Browser end-to-end | 34 passed |
| Grounding evaluation | 7/7 invariants hold |
| Documentation audit | 95 claims check out |
| ruff / mypy / import-linter | clean, 4 contracts kept |
| gitleaks over **60 commits** of history | **no leaks found** |
| `pip-audit --strict` / `pnpm audit` | no known vulnerabilities |

The live abuse run covers concurrent submissions from one session, isolation between sessions,
cross-session access on every task route, burst refusal, a genuine Redis stop (limiter fails
open, submission returns 503 with no broker detail, readiness flips to not-ready) and recovery
without an API restart.

## 4. Repository and licence audit

- **No credential in the working tree or in history.** gitleaks scanned 60 commits and ~2.25 MB:
  no leaks. No `.env` has ever been added in any commit; only `*.example` files are tracked.
- **Committed test evidence carries no visitor content.** The live-evidence exports hold task
  ids, model names, token counts, costs and retrieved public URLs — no question text, no
  session ids, no credentials.
- **Licences.** Project: MIT. Python dependencies: 42 MIT, 15 BSD-3, 6 Apache-2.0 and similar.
  Reciprocal licences present: `psycopg*` (LGPL-3.0-only) and `certifi`, `orjson`, `tqdm`,
  `pathspec` (MPL-2.0). Both are compatible with an MIT project **as used** — unmodified
  libraries, no static linking, nothing distributed as a binary. JavaScript production
  dependencies: 109 MIT, 1 ISC, no copyleft.
- **Advisories:** none open in either ecosystem. There are no accepted exceptions.
- **The repository is private**, with 0 forks, and remains so.

## 5. Remaining deployment blockers

1. **Secret handling has never been executed.** The procedure is written; no host exists.
2. **No host has been provisioned.** Requires approval, and a credit card for Oracle's
   identity check.
3. **TLS has never been obtained.** The Caddy configuration is written but has not answered an
   ACME challenge.
4. **Backups have never been taken or restored.** The commands are written and untested
   against a real dataset.
5. **Rollback has never been exercised**, including the migration-downgrade path.
6. **The deployment has no abuse history.** Rate limits are tested against synthetic bursts
   from one machine, not real traffic.
7. **Proxy headers are not trusted or validated.** The rate limiter keys on the session cookie
   rather than the client IP, so a spoofed `X-Forwarded-For` cannot currently affect anything —
   but if IP-based limiting is ever added, the trust boundary needs settling first.

## 6. Proposed deployment steps

Subject to approval; nothing below has been done.

1. Provision the VM (Oracle Cloud Always Free, ARM). **Needs approval and a card.**
2. Install Docker Engine and the compose plugin. Open 80/443 only, in both the cloud
   firewall and the host firewall.
3. Point DNS at the host and confirm propagation before starting Caddy.
4. Clone, `cp deploy/.env.production.example .env`, generate three secrets, `chmod 600`.
5. Start with the production overlay. Confirm the rendered config publishes nothing but Caddy.
6. Verify: `/ready`, then `scripts/smoke-test.sh`, then `scripts/abuse-check.sh`.
7. Take a backup and **restore it into a scratch database** to prove the procedure.
8. Exercise a rollback to the previous commit and back.
9. Only then consider enabling live providers, with `DEMO_ACCESS_CODE` set.
10. Make the repository public as a separate, explicit decision.

## 7. Commits

```
5dd7c9a  feat(security): enforce the four admission gates, and fail cleanly on a dead queue
4df8a20  fix(security): serialise admission, and bind nothing public but Caddy
```

(plus this report and the deployment documentation)

## Sources

Free-tier terms were verified against vendor documentation in November 2026, not from memory:
[Render service types](https://render.com/docs/service-types),
[Fly.io pricing](https://docs.fly.io/about/pricing),
[Neon free plan limits](https://neon.com/faqs/free-plan-limits-and-quotas),
[Upstash](https://upstash.com/), and Oracle's Always Free resource list. Free tiers change
often; re-check before relying on any of this.
