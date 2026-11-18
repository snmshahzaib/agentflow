# Grounding evaluation

A deterministic harness that scores what the pipeline does with evidence.

```bash
docker compose up -d postgres redis
scripts/with-services.sh uv run python scripts/run_evals.py
```

It needs no credentials and costs nothing: the model is the deterministic stub and retrieval is
the committed fixture corpus. The run writes
[`evaluation-baseline.json`](evaluation-baseline.json) and exits non-zero if an invariant
fails.

## What this measures — and what it does not

**It measures the grounding pipeline.** Every figure is a property of how AgentFlow handles
evidence: whether a citation resolves to a source that was actually retrieved, whether a claim
that cannot be grounded is marked unsupported rather than asserted, whether the report's
limitations state the true counts.

**It is not a benchmark of research quality.** On the offline profile the stub's claims are
fixed per stage; what varies between cases is the *evidence* each question retrieves, and
therefore whether those fixed claims can be grounded in it. That is exactly the mechanism worth
testing, and it says nothing about how good an answer is. No score here is a statement about a
live model's output.

The same metrics would run unchanged against a live provider, which is how a later milestone
could compare models — but nothing here has been run against one, and the committed baseline is
fixture-only.

## The cases

Each case carries **its own plan**. The stub planner returns the same four search queries
whatever it is asked, so a first version of this harness had all seven cases retrieve an
identical eight sources — seven cases measuring one thing. A case now supplies its subtasks as
fixture content, and the rest of the pipeline runs for real.

| Case | Pressure applied |
|---|---|
| `broad_evidence` | Four subtasks across the corpus: the widest evidence set |
| `single_subject` | One tool only, so claims about the others cannot be grounded |
| `scarce_evidence` | One subtask capped at a single result — the hardest grounding test |
| `no_methodology` | A document that explicitly publishes no methodology |
| `off_corpus` | A question nothing in the corpus answers |
| `injected_evidence` | Retrieves the red-team document carrying an injected directive |
| `duplicate_pressure` | Overlapping queries, so the same page surfaces twice |

## The metrics

Two kinds, and they are not interchangeable.

**Invariants must hold.** A failure is a defect, and the run exits non-zero.

| Metric | Meaning |
|---|---|
| `citation_resolution` | Every claim→source link points at a source retrieved for that task. Below 100% means a fabricated citation survived into the database. |
| `grounded_support` | Every `supported` or `partial` verdict cites at least one retrieved source. Support with nothing to point at is not support. |
| `source_uniqueness` | The same page is never stored twice for one task. |
| `disclosure_accuracy` | The limitations section states the run's real counts — sources, subtasks, unsupported claims, provider — and never calls claim checking a fact check. |
| `provenance_notice` | The notice describes the actual mix of live and fixture evidence. |

**Observations are reported without a threshold.** A high `unsupported_rate` is the system
being honest about thin evidence, not the system failing.

| Metric | Meaning |
|---|---|
| `unsupported_rate` | The share of claims the system declined to support. |
| `evidence_utilisation` | The share of retrieved sources a claim actually cited. |

## The baseline

```
case                    src  clm   cite   grnd   uniq  unsup   util
broad_evidence            9    5   100%   100%   100%    20%    44%
single_subject            5    5   100%   100%   100%    20%    80%
scarce_evidence           1    5   100%   100%   100%    80%   100%
no_methodology            1    5   100%   100%   100%    80%   100%
off_corpus                2    5   100%   100%   100%    60%   100%
injected_evidence         5    5   100%   100%   100%    20%    80%
duplicate_pressure        5    5   100%   100%   100%    20%    80%
```

The result worth having: as evidence shrinks from nine sources to one, the share of claims the
system declines to support rises from 20% to 80%, while citation integrity never leaves 100%.
A claim citing `S4` when retrieval returned one source is downgraded, not asserted.

`injected_evidence` is the case to look at twice. The red-team document is genuinely in the
evidence set, and every invariant still holds.

## How it is enforced

`backend/tests/test_evaluation.py` runs in CI:

- **Eight tests prove each metric can fail** — an unresolvable citation, a half-fabricated
  citation set, a positive verdict with no source, a duplicated source, a report stating the
  wrong counts. A metric that cannot fail is measuring nothing.
- **Three tests run the suite through the real graph**, asserting every invariant holds, that
  scarce evidence raises the unsupported rate, and that hostile evidence does not break
  grounding.

## Known limits of this harness

- **The claims are fixed.** Only the evidence varies, so the harness cannot say anything about
  how well a model reasons.
- **The corpus is twelve documents.** It is enough to create real scarcity and overlap, not
  enough to be representative of the web.
- **No live-provider baseline exists.** The code path is provider-agnostic, but it has not been
  run against a live model, and no such figures are published.
