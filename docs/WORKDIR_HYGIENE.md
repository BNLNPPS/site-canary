# PanDA work-area hygiene job

## Decision and ownership

PanDA payloads create their work directories under site-provided filesystems.
When a pilot exits normally, the pilot wrapper removes its directory.  A hard
kill, OOM kill, batch eviction, or node failure can prevent both pilot and
wrapper cleanup from running.  Successful cleanup in a normal pilot log and
the accumulation of orphan directories are therefore consistent observations.

The experiment should clean up what its jobs created when the site grants the
ordinary payload identity permission to do so.  The first Canary remediation
payload is consequently a PanDA job, sent to the affected queue and running
with the same filesystem rights as other ePIC payload jobs.  It has no root,
SSH, or site-administrator authority.

Canary owns the control loop: discovery, diagnosis, dispatch of the bounded
cleanup payload, result collection, and verification.  A site administrator is
needed only where the normal payload identity cannot remove its own remnants,
or where node-local storage cannot be reached by an ordinary job landing
elsewhere.

## Filesystem topology

The cleanup model applies directly to a shared work root such as the reported
GREX path `/project/6041615/condorjobs`: a Canary job within that root can see
and remove sibling workdirs owned by the same execution identity.

A job cannot clean node-local filesystems on other workers.  For a node-local
work root, the same payload can clean the node on which it lands, but complete
coverage requires per-node dispatch or a site facility.  Canary must record
the observed topology and must not present a single landing as site-wide
coverage in that case.

## Control loop

1. Passive accounting detects proxy symptoms such as a queue-wide failure
   burst involving `ENOSPC` or file-creation errors.
2. A scan-only hygiene job lands at the queue and measures bytes and inodes on
   the actual payload work filesystem.
3. The job inventories top-level directories that match the configured PanDA
   workdir contract and classifies safe cleanup candidates.
4. Canary ingests the report and presents the evidence and proposed action.
5. Under declared policy, a second job runs the same payload with deletion
   enabled.  Initial commissioning remains explicitly human-gated.
6. The cleanup job re-measures the filesystem; Canary records the observed
   inode and byte recovery and schedules another scan if pressure remains.

The scan and cleanup are separate PanDA jobs so discovery never implies
deletion.  The cleanup job repeats all checks rather than trusting candidates
from an earlier report.

## Payload interface

The implemented payload is:

```text
canary workdir-hygiene
```

This command's execution path uses only the Python standard library, allowing
the Canary source to be staged with a PanDA job without installing the store,
policy, or controller dependencies on the worker.

Scan-only is the default.  Inside a PanDA workdir, the portable form is:

```bash
canary workdir-hygiene \
  --root-parent-of-cwd \
  --min-age-hours 168 \
  --output canary-workdir-hygiene.json
```

Deletion is an additional, explicit mode:

```bash
canary workdir-hygiene \
  --root-parent-of-cwd \
  --min-age-hours 168 \
  --max-job-runtime-hours 24 \
  --safety-margin-hours 24 \
  --max-delete 10 \
  --delete \
  --output canary-workdir-hygiene.json
```

The runtime value is not a guess: the submitter must supply the queue's
enforced maximum job runtime.  Delete mode refuses to run unless
`minimum age >= maximum runtime + safety margin`.

An exact root can be supplied with `--root`, primarily for a configured site
profile.  `--root-parent-of-cwd` avoids embedding site paths: the current
Canary workdir is a child of the same root as the abandoned siblings and is
automatically protected.

## Candidate contract and safety invariants

The initial ePIC workdir name contract is the full-match regular expression
`eic_[A-Za-z0-9]+`.  It is configurable because the observed site convention
must be confirmed before cleanup is commissioned.

A directory is eligible only when every condition holds:

- it is an immediate child of the resolved work root;
- its complete name matches the configured expression;
- it is a real directory, not a symbolic link;
- it is owned by the cleanup payload's effective user;
- it is on the same filesystem as the work root;
- its top-level modification time is older than the configured minimum age;
- it is neither the cleanup job's own workdir nor an explicitly protected
  workdir.

Delete mode adds these invariants:

- the cleanup payload itself must be running below the selected work root;
- the minimum age must exceed the queue's enforced maximum runtime by the
  configured safety margin;
- a filesystem lock permits only one deleting cleanup job per work root;
- the directory device, inode, and modification time must be unchanged after
  selection;
- a pre-delete walk refuses to cross a nested filesystem boundary;
- the number of top-level directories removed in one run is capped;
- the report limit must be large enough to name every possible deletion.

Deletion never widens permissions and never follows a symlink.  Entries with
another owner are reported but not touched.  These rules intentionally prefer
leaving an orphan behind to deleting an active or unrelated directory.

Directory age is based on the top-level directory modification time.  That is
safe only in combination with an enforced maximum runtime longer than any live
job can have existed; age alone is not an active-job oracle.  A later control
plane integration may also pass active workdir paths through the repeatable
`--protect` option, but active inventory is additional protection rather than
a substitute for the runtime envelope.

## Report

The payload emits `canary-workdir-hygiene-report/0` JSON to stdout or the file
named by `--output`.  It contains:

- resolved root, current workdir, mode, version, and generation time;
- hostname, execution identity, and available non-secret PanDA job context;
- the complete selection and deletion policy used by the job;
- filesystem byte and inode statistics before and after;
- observed changes in free bytes and free inodes;
- classification totals for matching, eligible, protected, young,
  owner-mismatched, symlinked, and cross-filesystem entries;
- a bounded per-workdir audit list with `would_delete`, `deleted`,
  `delete_error`, `delete_limit`, or `skipped` action;
- deletion totals, errors, and eligible directories left for another run.

The filesystem delta is an observation, not an attribution guarantee: other
jobs can allocate or release files concurrently.  It is nevertheless the
right verification measurement on the affected filesystem.

## PanDA integration still to build

The payload and report contract are now present.  The next implementation is
the controller side of increment 8:

- a queue hygiene profile carrying the workdir naming expression, enforced
  maximum runtime, safety margin, filesystem topology, and cleanup cap;
- PanDA submission through the dedicated Canary global share, targeting one
  queue and staging the JSON report;
- report ingestion and persistence as capability/health evidence;
- inode-pressure and cleanup-result presentation on the Canary page;
- deterministic trigger and approval policy for scan and deletion jobs;
- action-stream provenance for submission, deletion, and verification.

No central process needs write access to the site filesystem.  It submits and
observes; the PanDA payload performs the filesystem operation locally.

## GREX commissioning sequence

Before the first deleting job:

1. Confirm that `/project/6041615/condorjobs` is shared by GREX workers and
   that the cleanup job identity can remove abandoned sibling directories.
2. Confirm the complete pilot workdir naming expression and the queue-enforced
   maximum runtime.
3. Submit a scan-only job with a seven-day minimum age and compare its bounded
   inventory with the site administrator's observation.
4. Submit delete mode with `--max-delete 1`; verify the named directory was an
   orphan and check the reported inode delta.
5. Increase the cap conservatively and repeat until inode pressure clears.
6. Retain periodic scan coverage and use cleanup only when the declared policy
   calls for it.

This sequence validates access, naming, topology, and age assumptions on the
real site before the mechanism is allowed to remove at scale.
