"""Grounding evaluation for the research pipeline.

What this measures, and what it does not, stated plainly because the
distinction is easy to blur:

**It measures the grounding pipeline**, not the intelligence of a model. Every
figure here is a property of how AgentFlow handles evidence -- whether a
citation resolves to a source that was actually retrieved, whether a claim
that cannot be grounded is marked unsupported rather than asserted, whether
the report's limitations section states the true counts.

**It is not a benchmark of research quality.** On the default offline profile
the model is the deterministic stub, whose claims are fixed per node; what
varies between cases is the *evidence* the fixture provider returns for each
question, and therefore whether those fixed claims can be grounded in it. That
is precisely the mechanism worth testing, and it is not a measure of how good
an answer is. No score here says anything about a live model's output quality.

The same metrics run unchanged against a live provider, which is how a future
milestone could compare models -- but nothing in this package has been run
against one, and the committed baseline is fixture-only.
"""
