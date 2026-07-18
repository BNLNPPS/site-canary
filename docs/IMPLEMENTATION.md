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
increment 7).
