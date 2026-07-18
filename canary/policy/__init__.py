"""The policy engine: declared policy in, reproducible verdicts out.

The policy is a compact, versioned document (design principle 6); the
evaluator is deterministic (principle 7). Verdicts are recorded, and
status transitions follow them — never overriding a manually pinned
status, and never recovering an excluded queue from passive evidence
alone.
"""
