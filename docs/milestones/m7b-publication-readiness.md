# M7B — publication readiness

**Status: ready for publication, pending one decision. The repository is still private and its
visibility has not been changed.**

Everything below was verified on 2026-11-19 against commit `7c9cf9f`.

## 1. Security audit

### Result

| Check | Result |
|---|---|
| gitleaks over full history | **62 commits, ~2.29 MB, no leaks** |
| `.env` or key material tracked | none — only `*.example` files |
| `.env` ever committed | never, in any commit |
| Credential-shaped strings in tracked files | none |
| Personal machine paths (`/Users/...`) in tracked files | none |
| Work email or real name beyond the commit identity | none in the tree, any diff, or any commit message |
| Current history authorship | one identity: `Shahzaib Nadeem <61820487+snmshahzaib@users.noreply.github.com>` |
| Committed logs, dumps or databases | none |
| Screenshot EXIF / text metadata | none — no embedded paths or usernames in any of the nine |
| Fixture corpus hosts | all `fixtures.agentflow.test` (reserved TLD), no real hosts |
| Exported live evidence | task ids, model names, token counts, costs, public URLs — **no question text, session ids or credentials** |
| Python advisories (`pip-audit --strict`) | none |
| JavaScript advisories (`pnpm audit`) | none |
| Accepted advisory exceptions | **none** |

### GitHub Actions

| Property | State |
|---|---|
| `secrets.*` references | **none anywhere** — CI needs no credential |
| `permissions:` | `contents: read` (least privilege) |
| `pull_request_target` | **not used** — the trigger that makes fork PRs dangerous on public repos |
| Secret echoing (`set -x`, `echo ${{ }}`) | none |
| Default profile in CI | `LLM_PROVIDER=stub`, `SEARCH_PROVIDER=fixture` — network-free |

A fork PR will run with a read-only token and no secrets. Nothing needs changing before the
repository becomes public.

### The one finding that needs a decision

**Force-pushed commits remain retrievable from GitHub by exact SHA.**

Two history rewrites happened earlier in the project — one to correct commit authorship (M1),
one to strip assistant co-author trailers (M5). Both used `--force-with-lease`. GitHub retains
the replaced commits as unreachable objects, and they are still fetchable through the API by
their exact SHA:

| Replaced history | What it holds | Impact if the repo becomes public |
|---|---|---|
| The pre-trailer-removal tip and its ancestors | The same code, with assistant co-author trailers in the messages | Attribution only. Contradicts the single-author intent; not a security issue. |
| The pre-identity-fix tip and its ancestors | Two commits authored under a previous, incorrect identity | Exposes a personal detail that does not belong on this repository. |

The specific commit identifiers are deliberately not recorded in this repository: publishing
them would hand over the only thing needed to retrieve the objects. They are kept in a private
note outside the working tree.

Three things bound this risk:

1. **The remote holds only `refs/heads/main`.** `git ls-remote` shows no backup refs and no
   tags. A clone and a search see the current history only.
2. **The commits are unreachable**, so they are not listed in the UI, not included in a clone,
   and not indexed by commit search. Retrieval requires knowing the exact 40-character
   identifier.
3. **No published artefact records those identifiers.**

The local backup references (`refs/backup/*`, `refs/original/*` and one lightweight tag) exist
**only on the author's machine** and present no publication concern by themselves. The one
latent risk is the tag: a deliberate `git push --tags` would publish the pre-rewrite history.
Nothing in the configured workflow does that.

**Recommended, pending your approval** — nothing has been done:

- **Before publishing:** ask GitHub Support to garbage-collect unreachable objects on the
  repository. This is a routine request and is the only way to remove them server-side.
- **Or accept it**, on the basis that the exposure requires a SHA nobody has, and the content
  is a work email on old commits rather than a credential.
- **Either way:** do not push tags. Deleting the local tag would remove the footgun, but that
  is a history-adjacent action and has not been taken.

## 2. Verified test results

Every figure is from a run on 2026-11-19, not an estimate.

| Suite | Result |
|---|---|
| Backend unit | **282 passed** |
| Backend integration | **170 passed** |
| **Backend total** | **452 passed**, 20 skipped (live tests, no credentials configured) |
| Frontend components (Vitest) | **58 passed** |
| Browser end-to-end (Playwright) | **34 passed** — 33 Chromium + the journey on Firefox |
| Grounding evaluation | **7/7 cases pass every invariant** |
| Live abuse run (`scripts/abuse-check.sh`) | **21 checks passed, 0 failed** |
| Docker smoke test | PASSED |
| Docker restart-recovery test | PASSED |
| ruff check + format | clean |
| mypy (`app`, `evals`) | clean, 74 source files |
| import-linter | 4 contracts kept, 0 broken |
| ESLint (`--max-warnings 0`) + `tsc -b` | clean |
| Mermaid diagrams | **7 parse cleanly** |
| Documentation audit | **102 claims check out** |
| GitHub Actions CI | all 5 jobs green |

The documentation audit mechanically checks that the test counts quoted in the README and
`docs/testing.md` match what the suites actually collect, so the figures above and the figures
in the README cannot drift apart silently.

## 3. Reproducibility, verified from a clean clone

A fresh `git clone` of the private repository, on isolated ports, with **no credentials of any
kind**:

| Step | Result |
|---|---|
| `cp .env.example .env` | three placeholders remain, none needed for the default profile |
| `docker compose up --build` | five containers, all healthy |
| `GET /ready` | `ready: true` — database, queue and LLM all up |
| `GET /api/meta` | `offline_mode: true`, `llm: stub`, `search: fixture` |
| `scripts/smoke-test.sh` | PASSED |
| `scripts/abuse-check.sh` | 21 passed, 0 failed |
| `scripts/with-services.sh uv run pytest` | 452 passed, 20 skipped |
| `scripts/run_evals.py` | 7/7 cases pass |
| `pnpm install && test && lint && typecheck && build` | 58 passed, all clean |
| `pnpm exec playwright test` | 34 passed |

No step required an API key, an account, a payment card, or editing any file beyond copying
`.env.example`. No personal path or machine-specific value appears anywhere in the checkout.

The optional free OpenRouter and Tavily configuration is documented separately, in the README
and in `docs/model-configuration.md`, and is not needed to run anything.

## 4. Repository presentation

| Item | State |
|---|---|
| README | Rewritten for a portfolio reader: problem, solution, capabilities, architecture with diagrams, nine screenshots of the real application, demo walkthrough, verified test results, security and abuse prevention, limitations, status |
| `LICENSE` | MIT, 2026 Shahzaib Nadeem |
| `CONTRIBUTING.md` | **new** — how to run every check, and the house rules the codebase is built on |
| `SECURITY.md` | **new** — private reporting, scope, and what the design does and does not guarantee |
| `.github/ISSUE_TEMPLATE/bug_report.yml` | **new** — asks which profile was in use |
| `.github/ISSUE_TEMPLATE/documentation.yml` | **new** — a documentation mismatch is a real defect here |
| `.github/ISSUE_TEMPLATE/config.yml` | **new** — routes security reports privately |
| `.github/pull_request_template.md` | **new** — requires pasted output, not "should work" |
| `.gitignore` | 110 active rules; `.env`, `.env.*`, `*.env`, build output, caches, Playwright artefacts |
| `.env.example` | 49 documented settings; audited mechanically against `Settings` |
| Repository description | **set** |
| Repository topics | **set**, 15 |

Deliberately not added: a code of conduct, a changelog, a roadmap, funding files or badges
beyond what is true. This is a fixed-scope portfolio project, and governance boilerplate would
be presentation rather than substance.

### Recommended description

> Human-in-the-loop multi-agent research orchestrator that refuses to assert what it cannot
> cite. FastAPI + LangGraph + arq, durable approval gates, a provider-agnostic LLM gateway,
> and citations re-bound to sources actually retrieved.

(233 characters; already applied to the private repository.)

### Recommended topics

`ai-agents` · `llm` · `langgraph` · `fastapi` · `human-in-the-loop` · `multi-agent-systems` ·
`python` · `react` · `typescript` · `postgresql` · `redis` · `docker` · `openrouter` · `rag` ·
`playwright`

(Already applied.)

## 5. Remaining risks

**For publication:**

1. **Unreachable force-pushed commits** (section 1). Needs your decision: request GitHub GC,
   or accept.
2. **The local backup tag could be pushed by accident.** A `git push --tags` would publish
   pre-rewrite history deliberately. Not removed, because that needs approval.

**In the project itself, and stated in the README:**

3. **Nothing is deployed.** No hosted instance exists, and the README says so.
4. **No performance or load figures.** None are measured, so none are claimed.
5. **Live evidence is from free models only.** The paid profile is implemented, disabled and
   untested.
6. **Claim checking is not fact checking.** A wrong source yields a wrong claim marked
   "supported".
7. **No screen-reader testing** with VoiceOver or NVDA.
8. **One browser engine** for 33 of 34 browser tests.
9. **Retention has never deleted a real expired task** — nothing anywhere is seven days old.
10. **Hosted secret handling has never been executed**, because nothing has been hosted.

None of these are concealed: items 3–10 appear in the README's limitations section or in
`docs/security.md`.

## 6. Publication checklist

Everything here is done except the two items that need your decision.

- [x] gitleaks clean over full history
- [x] No credential, `.env`, log, dump or database tracked
- [x] No personal path, work email or real-name exposure in the current history or tree
- [x] Screenshots carry no metadata; fixtures use a reserved TLD
- [x] Exported evidence carries no visitor content
- [x] Dependencies audited; no advisories, no accepted exceptions
- [x] Licences compatible as used (MIT project; `psycopg` LGPL and four MPL-2.0 packages used
      unmodified; JS production dependencies 109 MIT / 1 ISC)
- [x] GitHub Actions carries no secret, uses least privilege, avoids `pull_request_target`
- [x] README accurate; test counts machine-checked against the suites
- [x] LICENSE, CONTRIBUTING, SECURITY, issue and PR templates in place
- [x] Description and topics set
- [x] Clean-clone reproducibility verified with zero credentials
- [x] Full verification suite green, CI green
- [ ] **Decide on the unreachable force-pushed commits** (GitHub GC request, or accept)
- [ ] **Decide on the local backup tag** (leave, or delete)
- [ ] **Flip visibility to public** — explicitly out of scope for M7B

## 7. Commits

```
7c9cf9f  docs: add the files a public repository needs
```

Plus this report. The security audit, reproducibility verification and repository metadata
required no code changes — which is the result worth reporting: nothing had to be removed,
redacted or rewritten to make this publishable.
