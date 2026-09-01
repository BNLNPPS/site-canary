"""The policy engine: declared policy in, reproducible verdicts out.

The policy is a compact, versioned document (design principle 6); the
evaluator is deterministic (principle 7). Verdicts are recorded, and
queue status follows the latest verdict unless it is manually pinned.
"""
