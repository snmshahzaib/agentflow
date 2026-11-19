# Security

## Reporting a vulnerability

Please report security issues through
[GitHub's private vulnerability reporting](https://github.com/snmshahzaib/agentflow/security/advisories/new)
rather than opening a public issue.

A reply can be expected within a week. This is a personal portfolio project with no service
behind it, so there is no on-call rotation and no bounty.

## Scope

**AgentFlow is not deployed anywhere.** It has only ever run on a local Docker host. There is
no production service, no hosted instance and no user data. A report therefore concerns the
code as published, not a running system.

In scope: anything that could compromise a *self-hosted* instance — the prompt-injection
boundary, the citation-binding logic, session scoping, the admission gates, dependency
vulnerabilities, or the container and proxy configuration.

Out of scope: the deliberate non-goals below, and the known limitations recorded in
[`docs/security.md`](docs/security.md).

## What the design actually guarantees

Stated precisely, because overstating it would be the real vulnerability:

- **Retrieved web content is untrusted input.** It is fenced and labelled as data, never
  placed in a system prompt, and the model is offered no tools at synthesis time.
- **No URL is dereferenced server-side.** The search provider returns excerpts and the
  application uses them, which removes the SSRF surface rather than mitigating it.
- **Citations are re-bound to sources actually retrieved.** A model cannot invent a citation
  that survives, and a "supported" verdict with no resolvable source is downgraded to
  "unsupported".
- **Report content is rendered as hostile input.** No raw HTML is parsed, only safe URL
  schemes become links, and a strict CSP with no `unsafe-inline` or `unsafe-eval` applies.
- **Live research is gated.** Without a valid access code a task runs on the deterministic
  offline profile; the admitted providers are recorded on the task and the worker obeys that
  record rather than re-reading configuration.

**Prompt injection is contained, not prevented.** Whether a model obeys an injected
instruction is a property of that model. What is tested is that obeying does not help: a
deliberately compromised model still cannot self-approve a report, its fabricated citations
still do not resolve, and the generated limitations still report the truth.

**Claim checking is not fact checking.** It establishes that a retrieved source states a
claim. A wrong source yields a wrong claim marked "supported".

## Before self-hosting publicly

Five gates are documented in [`docs/security.md`](docs/security.md#deployment-blockers). Four
are implemented and tested; hosted secret handling has never been executed because nothing has
ever been hosted. Read that section before exposing an instance to the internet.

## Secrets

No credential is tracked in this repository, and none is baked into an image. gitleaks runs
pre-commit and in CI over full history. Only `*.example` files are tracked; real configuration
lives in an untracked `.env`.
