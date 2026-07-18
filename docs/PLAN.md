# site-canary Implementation Plan

The increment plan for site-canary implementation. The design and its
decision record are in [DESIGN.md](DESIGN.md); IMPLEMENTATION.md records
the built system as components land. Increments follow the design's
priorities: passive assessment before active probing, verdicts before
actuation, with node measurement early because its output shapes the
schemas that follow.

## Standing constraints

- **snapper-ai publication.** site-canary is a component owner feeding
  [snapper-ai](https://github.com/BNLNPPS/snapper-ai): health states,
  capability records, and the landscape map are published as bounded,
  owner-curated projections at declared resolution. The view of the
  distributed processing resources is a major extension of snapper-ai's
  snapshot view of the WFMS, and every state schema is shaped from the
  start so its projection publishes cleanly.
- **Family conventions.** PostgreSQL state store; models with UUID
  primary keys, `data` JSONField, `created_at`/`modified_at`;
  python-decouple configuration with the `CANARY_` prefix; the
  packaged-Django-app deployment pattern of snapper-ai for platform
  integration; standalone agent processes outside the web runtime.
- **prmon.** [prmon](https://github.com/HSF/prmon) is the node
  measurement tool. Schema design for capability records follows
  measured prmon output rather than preceding it.
- **Testing.** Verification is live usage: in-the-moment scripts and
  real runs. The repo carries one minimal smoke script (`tests/`)
  confirming the package imports and the CLI runs, and it stays at that
  scale. No test framework, fixtures, or mocking infrastructure.

## Increments

### 1. Scaffold (done)

`canary` Python package: pyproject packaging, python-decouple
configuration, logging, `canary` CLI entry point, simple functionality
tests. The layout anticipates the two deliverables: a packaged Django
app (models, migrations, REST, monitor pages) installable into the
swf-monitor runtime, and standalone agent processes for probing,
collection, and policy evaluation.

### 2. prmon landing kit (done)

A committed tool that characterizes the node it runs on: environment
fingerprint (platform, kernel, CPU, memory, container runtime, CVMFS,
GPU) plus a prmon-wrapped sample payload. Runnable on any node with no
PanDA machinery. Its measured output — what prmon reports, what is
stable, what discriminates nodes — defines the capability record and
packet schemas. It is the common base of the probe payload and the
rider.

### 3. State store v0 (done)

Django models in the packaged app for the map spine: sites, queues,
node environments, and landing reports, with ingest feeding the
two-level map — site level started by the first landing at a site,
node level built as landings accumulate distinct fingerprints. Tables
for passive samples, verdicts and status history, and capability
checks are added by the increments that produce their data, so schemas
follow measured output. The deployment contract for the
swf-monitor/swfdb installation is [SWF_INTEGRATION.md](SWF_INTEGRATION.md).

### 4. Passive assessor v0 (done)

Per-queue health metrics from PanDA accounting — time-to-start, failure
rate, throughput — computed on a cadence and written to the store,
building on the queue responsiveness measurement in
[PANDA_USER_JOBS.md](https://github.com/BNLNPPS/swf-epicprod/blob/main/docs/PANDA_USER_JOBS.md).
No actuation. The snapshot source decouples development from the PanDA
database; verification of the live source and the cadenced run are
platform-side steps.

### 5. Canary page (done)

The Canary page, in the System pulldown of the swf-monitor navigation:
the landscape map at site and node level, passive samples, and health
states, served by the packaged app's views and templates in the
swf-monitor runtime. Development runs against a local store; mounting
in the platform navigation lands with the installation.

### 6. Policy v0 (done)

The compact declared policy file (test classes, exclusion and recovery
windows, ePIC values) and the evaluator that turns stored evidence into
verdicts. Verdicts are logged and recorded, not actuated. Exclusion and
recovery windows, and probe test classes, enter the policy vocabulary
with the probe increment.

### 7. AI surface: snapper-ai publication and MCP

site-canary registers as a snapper-ai component owner and publishes its
first curated projections: per-queue health states and the capability
record. Canary MCP tools join the swf-monitor MCP service, serving
health states, capability records, and verdict provenance alongside the
platform's other operational state. A standalone MCP server wrapping
the packaged app's REST API is the path for deployments outside the
platform, should one be needed.

### 8. Actuation and probes

Verdicts act on PanDA queue status with action-stream provenance
records; the dedicated global-shares probe leaf; the first probe jobs
built from real ePIC payloads, carrying the landing kit.

### 9. Rider

The carrier-embedded rider: gather decision, packet schema publication,
collection ladder, collector deduplication. The rider extends the
node-level map to every node real work reaches.
