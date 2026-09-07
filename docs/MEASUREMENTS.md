# Node measurements

The measured capability of the processing landscape, per node
environment and per workload, from the jobs that ran there. A site is
judged against its own history and against the fleet rather than
against one other site's declaration. This document is the plan of
record for the measurement store; the design record is
[DESIGN.md](DESIGN.md) (the capability record, horizontal node
identity) and the built spine is [IMPLEMENTATION.md](IMPLEMENTATION.md).

## What is measured

Every finished ePIC production job leaves a payload report
(swf-epicprod EPICPROD_PAYLOAD.md § The payload report). Per stage it
carries what prmon measured: wall time, user and system CPU, peak
resident, proportional and virtual memory, bytes read and written; and
the job carries the events it simulated and reconstructed. The PanDA
job record carries where it ran: the queue, the node, the processor
description and the core count. Together these make every production
job a measurement of one node environment under one workload.

The measures kept, per stage of the payload:

- CPU seconds per event (user plus system CPU over the stage's events);
- wall seconds per event;
- CPU efficiency (CPU over wall, at the stage's core count);
- peak resident memory.

Failure rates are not measurements of capability and stay where they
are, in the queue page's observed section (swf-monitor
`queue_observed`) and the error record.

## Keys

A measurement is keyed by the environment and the workload, so that
like is compared with like.

Environment: the PanDA queue and the processor description the job
record carries (`cpuconsumptionunit`). This is a coarser identity than
the canary fingerprint, which production jobs do not yet carry; it is
what exists on every job today. A probe or payload canary lands with a
fingerprint, and its measurement is recorded against the node
environment as well. The rider (PLAN.md increment 9) closes the gap for
production jobs.

Workload: the container image the task ran, the detector version, the
physics configuration label (`pcNNN`), the payload version, and the
stage. The first four are resolved through PCS from the job's task; the
stage is the report's own.

## The store

One table in the canary store, `canary_node_measurement`, in the
family's conventions (UUID key, `data`, `created_at`, `modified_at`):

| Field | Meaning |
|---|---|
| `queue` | the canary Queue, created on first sight as the assessor does |
| `processor` | the processor description |
| `node_environment` | the NodeEnvironment when a fingerprint was carried, else null |
| `container_image`, `detector_version`, `physics_config`, `payload_version`, `stage` | the workload |
| `metrics` | per measure: count, mean, variance, min, max, kept as a running distribution |
| `jobs` | jobs contributing |
| `first_job_at`, `last_job_at` | the span of the evidence |

Unique on the environment and workload keys. A distribution is updated
in place per job (Welford's running mean and variance), so the row
stays one row however many jobs it summarizes, and the spread is kept
because the same node measured hours apart has differed threefold
(inflight record, 2026-09-06). The rows are read directly from the
store by pages, an ordinary database read.

## The ingest

A production-ops agent doer, `node-measure-ingest.py` in swf-monitor,
handler `node_measure_ingest`, hourly by cron enqueue on the prod-ops
pattern (swf-epicprod EPICPROD_OPS_AGENT.md). One pass reads the payload
reports of the jobs finished since its cursor, from the PanDA metatable
joined to the job record for the environment fields, resolves the
workload keys through PCS (PandaTasks by JEDI task id, its executed
container, its ProdTask's dataset for the detector version and physics
configuration, the report for the payload version), and folds each
job's stage measures into the matching rows. The cursor is the newest
job modification time folded, kept in a small JSON state file beside
the storage store, so a job is folded once and an overlap never counts
twice. The ePIC-specific resolution lives in the doer; the store and
its fold live in the canary package (`canary.store.measure`), which
knows nothing of PCS.

## Readers

- The queue detail page's observed section gains, per processor, CPU
  seconds per event for the queue's workloads, beside the failure rate
  and efficiency it already shows, read from the store.
- The scout gate and the dispatcher's judgment of whether a site is
  delivering at its known rate (swf-epicprod CONTINUOUS_PRODUCTION.md),
  and sizing, read the distributions.
- A canary projection into snapper-ai (PLAN.md increment 7) publishes
  the capability record at declared resolution.

Normalization against declared corePower and published benchmarks, and
the naming of outliers against the fleet, follow once the store holds a
week of production.

## Build order

1. This document.
2. The model and its migration in the canary store, then the fold
   (`canary.store.measure`: record one job's stage measures into the
   distributions).
3. The doer, the handler, the hourly enqueue; the first pass over the
   last seven days of finished jobs.
4. The queue page's observed section reading the store.
