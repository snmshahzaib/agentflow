# M8 — final publication checklist

**Status: prepared. The repository is private, its visibility is unchanged, no history has
been rewritten, no backup reference has been deleted, and the GitHub Support request has not
been sent.**

Verified on 2026-11-19 against commit `0e6008e`. Everything below is either a check that was
run, or a risk stated as a risk.

---

## A. Verified — checks that were run, with their results

### A1. Git history and references

| # | Requirement | Result |
|---|---|---|
| A1.1 | No secret anywhere in history | gitleaks: **62 commits, ~2.29 MB, no leaks** |
| A1.2 | No `.env` or key material tracked | none; only `*.example` |
| A1.3 | `.env` never committed | confirmed across all commits |
| A1.4 | Current `main` carries one identity | `Shahzaib Nadeem <61820487+snmshahzaib@users.noreply.github.com>` |
| A1.5 | No work email on `main` | 0 commits, 0 occurrences in any message or diff |
| A1.6 | No assistant co-author trailers on `main` | 0 |
| A1.7 | **Remote has no backup branch** | `git ls-remote --heads` → **1 head, `main` only** |
| A1.8 | **Remote has no tags** | `git ls-remote --tags` → **0** |
| A1.9 | Backup refs are local only | `refs/backup/*`, `refs/original/*`, one tag — none on the remote |
| A1.10 | The backup tag is lightweight | points directly at a commit, so `push.followTags` could not carry it even if enabled |

### A2. Nothing can push a tag or backup ref automatically

| # | Requirement | Result |
|---|---|---|
| A2.1 | No push refspec configured | `remote.origin.push` unset, locally and globally |
| A2.2 | `push.followTags` not enabled | unset, locally and globally |
| A2.3 | `push.default` not set to anything broad | unset |
| A2.4 | Remote is not a mirror | `remote.origin.mirror` unset |
| A2.5 | Fetch refspec is heads-only | `+refs/heads/*:refs/remotes/origin/*` |
| A2.6 | No Git hook runs `git push` or `git tag` | checked both installed hooks (`pre-commit`, `pre-push` — both are the pre-commit framework's runners) |
| A2.7 | No CI workflow pushes, tags or releases | no `git push`, `git tag`, `gh release` or release action anywhere in `.github/workflows/` |
| A2.8 | No workflow requests write permissions | every workflow is `contents: read` |
| A2.9 | Repository default workflow permission | **read**, and workflows cannot approve pull requests |

A plain `git push` therefore pushes the current branch and nothing else.

### A3. Repository content

| # | Requirement | Result |
|---|---|---|
| A3.1 | No committed log, dump or database | none |
| A3.2 | Screenshots carry no metadata | no `tEXt`/`iTXt`/`zTXt`/`eXIf` chunks in any of the nine |
| A3.3 | Fixtures use no real host | all `fixtures.agentflow.test` (reserved TLD) |
| A3.4 | Exported live evidence carries no visitor content | ids, models, tokens, costs, public URLs — no question text, session ids or credentials |
| A3.5 | No personal machine path | no `/Users/...`, `/home/...` or `C:\Users` in any tracked file |
| A3.6 | `.gitignore` covers the dangerous things | 110 rules: `.env`, `.env.*`, `*.env`, keys, certs, build output, caches, Playwright artefacts |
| A3.7 | `.env.example` complete | 49 settings, audited mechanically against `Settings` |

### A4. Dependencies and licensing

| # | Requirement | Result |
|---|---|---|
| A4.1 | Python advisories | `pip-audit --strict`: none |
| A4.2 | JavaScript advisories | `pnpm audit`: none |
| A4.3 | Accepted advisory exceptions | **none** |
| A4.4 | Project licence | MIT, 2026 Shahzaib Nadeem |
| A4.5 | Dependency licences compatible as used | Python: 42 MIT, 15 BSD-3, 6 Apache-2.0, plus `psycopg*` (LGPL-3.0) and four MPL-2.0 packages — all used unmodified, nothing statically linked or redistributed. JavaScript production: 109 MIT, 1 ISC, no copyleft |

### A5. Documentation

| # | Requirement | Result |
|---|---|---|
| A5.1 | Documentation audit | **105 claims check out** (links, scripts, settings, test counts) |
| A5.2 | Test counts match the suites | checked mechanically; a stale figure fails CI |
| A5.3 | Mermaid diagrams parse | **7 of 7** |
| A5.4 | README states what is not true | limitations section lists all ten, including that nothing is deployed |
| A5.5 | No fabricated metric, adoption figure or production claim | no performance figure is published anywhere, because none was measured |

### A6. Tests, all re-run for this checklist

| # | Suite | Result |
|---|---|---|
| A6.1 | Backend unit | **282 passed** |
| A6.2 | Backend integration | **170 passed** |
| A6.3 | Backend total | **452 passed**, 20 skipped (live tests, no credentials) |
| A6.4 | Frontend components | **58 passed** |
| A6.5 | Browser end-to-end | **34 passed** (33 Chromium + Firefox journey) |
| A6.6 | Grounding evaluation | **7/7 invariants hold** |
| A6.7 | Live abuse run | **21 checks, 0 failed** |
| A6.8 | Docker smoke test | PASSED |
| A6.9 | Docker restart-recovery test | PASSED |
| A6.10 | ruff check + format | clean |
| A6.11 | mypy (`app`, `evals`) | clean, 74 files |
| A6.12 | import-linter | 4 contracts kept, 0 broken |
| A6.13 | ESLint + `tsc -b` | clean |
| A6.14 | GitHub Actions CI | **all 5 jobs green** |

### A7. Reproducibility, from a clean clone

Verified by cloning the repository fresh, on isolated ports, with **no credentials of any
kind**: five healthy containers, `ready: true`, offline profile confirmed, smoke test passed,
21/21 abuse checks, 452 backend tests, 7/7 evaluation cases, 58 frontend tests, 34 browser
tests. No API key, no account, no payment card, and no file edited beyond copying
`.env.example`.

### A8. Repository metadata

| # | Item | State |
|---|---|---|
| A8.1 | Description | set (233 characters) |
| A8.2 | Topics | set, 15 |
| A8.3 | `LICENSE` | MIT |
| A8.4 | `CONTRIBUTING.md` | present |
| A8.5 | `SECURITY.md` | present, with private reporting route |
| A8.6 | Issue templates | bug report, documentation correction, config routing security reports privately |
| A8.7 | Pull request template | present, requires pasted output |
| A8.8 | Visibility | **private — unchanged** |

---

## B. Remaining risks — not resolved, stated plainly

### B1. Unreachable commits from the earlier identity correction

**The one item that needs a decision.** Two commits, replaced by the M1 identity correction,
are no longer part of any branch but remain retrievable from GitHub by their exact 40-character
identifier. They carry a previous, incorrect author identity — a personal detail that does not
belong on this repository. A further set of unreachable commits from the M5 trailer removal
carry assistant co-author trailers, which is an attribution matter rather than a security one.

**The identifiers are deliberately not recorded here.** Publishing them would supply the one
thing needed to retrieve the objects; they are kept in a private note outside the working tree.

What bounds the risk: the remote holds only `refs/heads/main`; the commits are unreachable, so
they are neither listed in the UI, included in a clone, nor indexed by commit search; and
retrieval requires an identifier that appears in no published artefact.

**Options:** ask GitHub Support to garbage-collect the unreachable objects before publishing,
or publish with the exposure accepted and documented. GitHub may decline — their stated bar
concerns credentials that cannot be rotated, and an address in commit metadata may fall outside
it. A prepared request is kept privately, outside this repository.

### B2. The local backup tag is a latent footgun

One lightweight backup tag exists locally. Nothing pushes it today (A2), and because it is
lightweight `push.followTags` could not carry it even if enabled — but an explicit
`git push --tags` would publish pre-rewrite history on purpose. It has **not** been deleted,
because that needs approval.

### B3. Project limitations, already published

These are in the README and are not blockers — they are what the project honestly is:

| # | Limitation |
|---|---|
| B3.1 | **Nothing is deployed.** It has only run on a local Docker host |
| B3.2 | No performance or load figures exist, so none are claimed |
| B3.3 | Live evidence is from free models only; the paid profile is implemented, disabled, untested |
| B3.4 | Claim checking is not fact checking — a wrong source yields a wrong claim marked "supported" |
| B3.5 | Prompt injection is contained, not prevented |
| B3.6 | No screen-reader testing with VoiceOver or NVDA |
| B3.7 | One browser engine for 33 of 34 browser tests |
| B3.8 | Retention has never deleted a genuinely expired task — nothing is seven days old |
| B3.9 | Hosted secret handling has never been executed |
| B3.10 | Three of the five deployment gates verified only against synthetic traffic from one machine |

---

## C. The publication sequence, when you decide to go

Nothing here has been done.

1. **Decide on B1.** Send the support request kept privately outside the repository, or
   accept the residual exposure.
2. If sent, **wait for the outcome** before changing visibility — garbage collection is
   pointless after the repository is public and has been cloned.
3. **Decide on B2.** Leave the local backup tag, or delete it.
4. Re-run the verification once more immediately before flipping, since time will have passed:
   `make verify`, `scripts/e2e-test.sh`, `scripts/abuse-check.sh`, `audit-docs.py`, gitleaks.
5. **Change visibility to public** — a manual step, explicitly out of scope for every
   milestone so far.
6. After publishing: confirm the README renders, the nine screenshots load, the Mermaid
   diagrams render on GitHub, and the issue templates appear in the new-issue chooser.
7. Confirm Actions still pass on the public repository, where fork pull requests run with a
   read-only token and no secrets.

## D. What was done in M8

| Item | State |
|---|---|
| GitHub Support request | **prepared, not sent** — kept in a private note outside the repository |
| Support contact route | GitHub Support portal, <https://support.github.com> |
| History rewritten | **no** |
| Backup references deleted | **no** |
| Repository visibility changed | **no** |
| Remote backup branches or tags | **verified absent** |
| Automatic tag or ref pushing | **verified impossible** under the current configuration |
| `main` and verified tests | **preserved** — all 452 backend, 58 frontend, 34 browser tests re-run and green |
