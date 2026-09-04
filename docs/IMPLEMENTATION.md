# site-canary Implementation

The record of the built system. The design record is
[DESIGN.md](DESIGN.md); the increment plan is [PLAN.md](PLAN.md).

## Package

The `canary` Python package (distribution name `site-canary`).
Configuration comes from python-decouple with env prefix `CANARY_`
(`canary/config.py`): `CANARY_LOG_LEVEL`, `CANARY_PRMON`,
`CANARY_CVMFS_REPOS`. The `canary` CLI (console script, or
`python -m canary`) grows subcommands as increments land; unrecognized
input is an error. Verification is live usage plus one smoke script
(`tests/run_tests.sh`).

## Landing kit

`canary landing [--payload-seconds N] [--no-payload] [-o FILE]`
characterizes the node it runs on and emits a landing report
(schema `canary-landing-report/0`): an environment fingerprint plus the
prmon summary of a sample payload run. It runs on any node with no
PanDA machinery and is the common base of the probe payload and the
rider.

### Fingerprint

`canary/landing/fingerprint.py` collects bounded facts: OS, kernel,
architecture, CPU model and logical core count, memory, containment and
available container runtimes, CVMFS reachability and revision per
configured repo, GPU presence and driver, glibc, python. The
fingerprint hash covers the environment-discriminating fields;
hostname is recorded as metadata outside the hash — identity is
horizontal (distinguish environments within the current map), per
DESIGN.md. Every external probe is a subprocess with a 5-second
timeout, and a collector failure is recorded as an error value in the
report rather than raised or dropped.

### prmon integration

`canary/landing/kit.py` resolves prmon from `CANARY_PRMON`, then PATH,
then the repo-local `.prmon/` populated by `scripts/fetch_prmon.sh`
(static release binary, ~5 MB, no dependencies — carriable in probe
payloads). The sample payload runs under `prmon --interval 1`; the JSON
summary is captured whole into the report, including prmon's version
and its hardware block, which adds CPU topology the fingerprint does
not collect itself: sockets, cores per socket, threads per core.

Measurement notes from prmon 3.2.0, informing the capability record
schema:

- **cpumon**: `utime`/`stime`/`wtime` give the payload's CPU
  efficiency directly.
- **iomon**: `rchar`/`wchar` are logical I/O, `read_bytes`/`write_bytes`
  physical. Reads served from page cache appear only in `rchar`, so a
  storage read check must defeat caching to measure storage.
- **netmon**: device-level — it counts whole-node traffic, not traffic
  attributable to the payload. Network figures are node-level facts and
  are recorded as such.
- **Avg** values of monotonic counters are rates (`B/s`); **Max**
  values are totals.

### Sample payload

`canary/landing/sample_payload.py`: a bounded CPU hash loop with
periodic file I/O, giving prmon meaningful work to measure. Probe jobs
built from real ePIC payloads replace it for probing (PLAN.md
increment 8).

## State store

`canary.store` is a packaged Django application (app label `canary`,
tables `canary_*`) holding the map spine:

- **Site** — the location grouping for queues and node environments,
  with first/last landing and a map recomputed from the node census.
- **Queue** — a PanDA queue served by a site, with its own health
  state.
- **NodeEnvironment** — the map's node level: one distinct execution
  environment at a site under horizontal identity (unique
  `(site, fingerprint)`), carrying the fingerprint content, prmon CPU
  topology, and a landing census.
- **LandingReport** — the evidence stream: each landing report as
  delivered, with source (probe, rider, manual) and landing time.

The queue-state vocabulary is unknown, insufficient, healthy, degraded,
failing. Model conventions follow the family: UUID primary keys,
`data` JSONField, `created_at`/`modified_at`, PROTECT foreign keys,
named constraints and indexes.

Ingest (`canary.store.ingest.ingest_report`) is one transaction per
report: the site record starts or updates, the node environment is
created or refreshed, the report is stored, and the site-level map is
recomputed deterministically from the current node census —
environments, landings, platforms, architectures, core and memory
ranges, GPU and CVMFS environment counts. A malformed report raises
`IngestError`; nothing ingests partially.

Store dependencies install with the `store` extra
(`pip install "site-canary[store]"`). The standalone harness
`scripts/storectl.py` (`check` | `makemigrations` | `migrate` |
`ingest` | `map`) configures Django from the `CANARY_DB_*` settings
(via `canary.store.standalone`) for development and standalone use; in
the swf-monitor deployment the host project owns settings and
migrations ([SWF_INTEGRATION.md](SWF_INTEGRATION.md)).

## Passive assessor

`canary assess (--snapshot FILE | --panda) [--window-days N]
[--min-jobs N] [--write] [--json]` computes the queue-responsiveness
instrument of PANDA_USER_JOBS.md per queue over the window: job count,
creation-to-start wait median and 90th percentile, failure rate,
finished-per-hour. Computation (`canary/assessor/metrics.py`) is a
pure function over accounting job rows; malformed rows are counted and
reported, never silently dropped. A queue below the statistics
threshold keeps its entry with null percentiles and a low-stats flag —
quiet queues are probe targets, not gaps.

Two sources (`canary/assessor/sources.py`) deliver identical rows:
`--panda` queries `doma_panda.jobsarchived4` (finished and failed jobs
since the window start) through `CANARY_PANDA_DSN`, and `--snapshot`
reads the same rows from a file (schema
`canary-accounting-snapshot/0`, written by `dump_snapshot`). The
snapshot is the relay between the platform host, which can export one
with a single query, and development anywhere. The live query is
verified against the BNL instance: the schema matches as written
(SWF_INTEGRATION.md).

`--write` stores one `PassiveSample` per assessed queue: typed columns
for the core instrument (`njobs`, `wait_median_s`, `wait_p90_s`,
`failure_rate`), the remainder in `metrics`. Queues are created on
first sight, site unset until the PanDA-configuration mapping arrives.
Rows from queues with `test` in the name are explicitly counted and
excluded; they produce no passive sample or policy verdict.

## Policy engine

The policy is a compact, versioned YAML document; the packaged ePIC
policy is `canary/policy/epic.yaml`, overridable via `CANARY_POLICY`.
It declares evidence requirements (maximum sample age, minimum job
count) and an ordered rule list of `field op value` conditions over
the passive metrics — parsed and validated at load time
(`canary/policy/loader.py`), never evaluated as expressions. Unknown
keys, missing evidence settings, missing or duplicate queue-state
rules, malformed boundaries, fields, and operators fail the load.

Evaluation (`canary/policy/engine.py`) separates the pure decision
from its application. `decide(policy, evidence)` returns the verdict
and its exact reason; stale evidence yields `unknown`, low statistics
yield `insufficient`. `apply()` records one `Verdict` per queue with
the full evidence and updates the queue status to that verdict with
`StatusChange` provenance. A manually pinned status is not overridden.
The advisory development policy has no sticky state: every current
sample can move an unpinned queue between healthy, degraded, failing,
insufficient, and unknown.

`canary evaluate [--policy FILE] [--write] [--json]` runs the
evaluator — dry run by default. Manual state setting goes through
`storectl set-status QUEUE STATUS [--pin|--unpin] [--reason ...]`,
recorded with `actor=manual`; `--pin` marks the status authoritative
against the evaluator. Verdicts and status apply per queue.

## Canary page

`canary.store.views.probes_page`, template `canary/probes.html`,
mounted in the swf-monitor navigation
([SWF_INTEGRATION.md](SWF_INTEGRATION.md)). One page serves the
canary: the site health table leads, and probe management follows as a
distinct section. The root canary URL redirects to this page. The
health table is public read-only, matching the System Status page. It is a
house-convention static table (`swf-sortable`, `swf_fmt` timestamps,
colored state cells) with the mapped site, current status, latest
passive sample, wait median and 90th percentile, failure rate,
window end, and Snapper link. Test-named queues are omitted. The table
caption states the status thresholds from the active policy
configuration and keeps that description within the table width. A
configuration error is logged and displayed on the page; no threshold
defaults are substituted. Queue states use BigMon-palette fill classes
from the platform's `state-colors.css`.

Development outside the platform: `scripts/webdev.py check|runserver`
renders the page against the `CANARY_DB_*` store using the
`scripts/devweb/` stand-ins for the base template and `swf_fmt`
filters. The stand-ins are dev-only and never installed hosted.

## Probes

`canary.probe` is the probe scheduling and dispatch module (PLAN.md
increment 8, first piece). Probe configuration lives on the store's
Queue rows (`data['probe']`: `enabled`, `interval_hours`); a queue
with no probe block is not probed. The schedule anchors on the last
submission that reached PanDA: a failed submission does not consume
the interval and retries on the next dispatch cycle, and a queue with
a run still awaiting its outcome is not probed again.

`canary probe-dispatch [--queue NAME ...] [--collect-only] [--json]`
collects the outcomes of open runs (below), then submits probes for
due queues (or immediately for named queues) through the submission
doer, recording one `ProbeRun` per attempt with trigger auto or
manual. A failed submission is recorded on its run with the doer
output, never raised past the dispatch loop; `--collect-only` runs the
collection alone.

The probe task is a single landing-kit job against the target queue.
`canary/probe_kit/build-sandbox.sh` vendors the canary package, as the
wheel the deploy builds beside the release, and the prmon binary into
`kit/` beside the release's production in-job runner
(`CANARY_DISPATCHER` overrides). The job runs the current campaign's
production container as PCS records it (the campaign from the campaign
status API, the container from its Standard Production configuration,
read through `SWF_MONITOR_URL` at dispatch), so a probe measures the
site and not the nightly image; when PCS cannot be reached the spec's
own `containerImage` is the fallback, and the run records which
container it used and why. One runner serves production and
probe jobs, and the task's exec invokes its canary mode as a single
command. The job fingerprints its node, runs the prmon-wrapped sample
payload, and delivers the landing report on two paths: embedded in
`jobReport.json`, which the pilot ships as job metadata to PanDA on
success and failure alike, and to stdout between
`CANARY-REPORT-BEGIN` and `CANARY-REPORT-END` markers, collectable
from the job log. Output goes to `group.EIC.canary.<queue>.<stamp>`
with processing type `canary`. The task allows one job attempt
(`maxAttempt` 1): a failed landing is a failed probe, not a job PanDA
retries until the signal is lost.

### Collection

Collection (`canary.probe.collect_run_outcomes`) brings every open run
up to date from PanDA at the start of each dispatch cycle. An open run
is a submitted one, or a finished one whose report has not been
collected. The run's job supplies the landing facts recorded on the
run: the landed site (PanDA's destination site for a pool queue, else
the queue's mapped site, else the queue name), the node, the
creation-to-start wait, the run time, and every non-zero error
component. A job that has started but not ended records its wait and
the run stays submitted. A finished job's metadata carries the landing
report, since the pilot ships `jobReport.json` whole into the PanDA
metatable; the report is ingested with source probe under the landed
site, and the run becomes `collected`, recording the report id, the
fingerprint hash, the kit exit code, CVMFS reachability per repository,
and GPU presence. A finished job without a report, or a report the
store rejects, leaves the run `finished` with the reason logged as an
error. A failed job fails the run with its error components; PanDA
keeps metadata only for finished jobs, so a failed probe's evidence is
its error components and the report markers in its log. A task with no
job record follows the task state. Collection reads PanDA through
`CANARY_PANDA_DSN`; a failure is recorded on the run and logged, never
raised past the loop, and the cycle's collection counts are reported
with the dispatch results.

The run history page shows each run's wait, run time, landed site and
node, and report summary beside its status and task; the probes page
shows the last run's wait.

The probe management section of the canary page lists each configured
queue with its probe health, last run, next automatic run, editable
interval, run count, and Run now and Disable controls, with an enable
form for unconfigured queues; each queue links to its probe run
history. Probe health is what the last completed probe delivered, in
the canary state vocabulary: healthy for a collected report with kit
exit 0, degraded for a report with a non-zero kit exit or a finished
job without a report, failing for a failed job, unknown when no probe
has completed or the last submission failed, with the reason as the
cell title. A run still open shows its phase and elapsed time, waiting
since submission or running since its start. The
controls are writes and follow the deployment's write gating. Run now
reports live: the page opens the relay stream for the agent's
`canary_probe_dispatch_complete` event and, when it arrives for the
queue, reloads so the row shows the run, submitted with its task or
failed with the reason in the run history; a failed dispatch says so
in place, and a cycle that reports nothing within fifteen minutes is
stated as such.
