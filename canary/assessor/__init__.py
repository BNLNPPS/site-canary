"""The passive assessor: per-queue health metrics from accounting data.

Where real workload flows, health metrics come free from accounting
(design principle 1). The assessor computes the queue-responsiveness
instrument — creation-to-start wait, failure rate, throughput — from
per-job accounting rows, identically whether the rows come from the
live PanDA database or a snapshot file.
"""
