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

- **Site** — the operational unit and the map's site level: health
  state, first/last landing, and a site map recomputed from the node
  census.
- **Queue** — a PanDA queue served by a site, with its own health
  state.
- **NodeEnvironment** — the map's node level: one distinct execution
  environment at a site under horizontal identity (unique
  `(site, fingerprint)`), carrying the fingerprint content, prmon CPU
  topology, and a landing census.
- **LandingReport** — the evidence stream: each landing report as
  delivered, with source (probe, rider, manual) and landing time.

The health vocabulary is unknown, healthy, suspect, excluded,
recovering. Model conventions follow the family: UUID primary keys,
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

## Policy engine

The policy is a compact, versioned YAML document; the packaged ePIC
policy is `canary/policy/epic.yaml`, overridable via `CANARY_POLICY`.
It declares evidence requirements (maximum sample age, minimum job
count) and an ordered rule list of `field op value` conditions over
the passive metrics — parsed and validated at load time
(`canary/policy/loader.py`), never evaluated as expressions. Unknown
keys, fields, or operators fail the load.

Evaluation (`canary/policy/engine.py`) separates the pure decision
from its application. `decide(policy, evidence)` returns the verdict
and its exact reason; stale evidence yields `unknown`, low statistics
yield `insufficient` (no status implication). `apply()` records one
`Verdict` per queue with the full evidence, and performs status
transitions with `StatusChange` provenance under the standing rules: a
manually pinned status is never overridden, and passive evidence does
not recover an excluded queue (exclusion stops the traffic that
generates it; recovery arrives with probes, or manually).

`canary evaluate [--policy FILE] [--write] [--json]` runs the
evaluator — dry run by default. Manual state setting goes through
`storectl set-status QUEUE STATUS [--pin|--unpin] [--reason ...]`,
recorded with `actor=manual`; `--pin` marks the status authoritative
against the evaluator. Verdicts and status apply per queue; site-level
status derivation arrives with actuation.

## Canary page

`canary.store.views.canary_page`, template
`canary/canary_page.html`, mounted in the System pulldown of the
swf-monitor navigation ([SWF_INTEGRATION.md](SWF_INTEGRATION.md)).
Public read-only, matching the System Status page. Three sections,
all house-convention static tables (`swf-sortable`, `swf_fmt`
timestamps, colored state cells): sites (status, environment and
landing counts, platforms, first/last landing), node environments
(fingerprint, platform facts, GPU, landing census, last seen), and
queues (status, latest passive sample: jobs, wait median and 90th
percentile, failure rate, low-stats flag, window end). Canary health
states carry BigMon-palette fill classes from the platform's
`state-colors.css`; excluded takes a calm neutral fill — a health
state is an evaluation, not a fact, and red means broken.

Development outside the platform: `scripts/webdev.py check|runserver`
renders the page against the `CANARY_DB_*` store using the
`scripts/devweb/` stand-ins for the base template and `swf_fmt`
filters. The stand-ins are dev-only and never installed hosted.
