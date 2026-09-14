# Node guard

The black hole guard at node level: a node that takes jobs and kills
them fast is found from the job record, recorded in the node map,
announced, and kept from taking more work. This document is the plan of
record for the guard; the design record is [DESIGN.md](DESIGN.md)
(Burn-through protection, Identity is horizontal) and the built spine
is [IMPLEMENTATION.md](IMPLEMENTATION.md).

## The problem it answers

A black hole node fails every job it takes within minutes, and a batch
system keeps landing pilots on it because a pilot that exits fast
leaves the slot free. Inside a queue that is otherwise healthy the
damage is invisible to a queue-level verdict until it is large: the
production crash record holds one node at a small site that killed 187
of 252 jobs, five nodes at a large site that killed 20,000 jobs of one
task over three days, and one cloud node that killed 144 jobs in an
hour and then recovered (swf-epicprod docs/SEGFAULT_DIAGNOSIS.md,
findings f-5, f-6, f-7, f-9). The retry budget of every job the node
touches is consumed; the task drains. The queue-level fast detectors of
the pressure front (burn-through, the failure window) answer a queue
that is sick as a whole and would stop feeding a healthy queue to
stop one node. The guard is the node-level instrument.

## Identity

The map's node identity is the fingerprint, which probe and payload
canary landings carry (DESIGN.md, Identity is horizontal). A production
job carries no fingerprint yet; its record names the batch host that
ran it (`modificationhost`, the slot prefix stripped) and the queue.
That host name is what a submit description excludes, so it is the
identity the guard keys on: a node is `(queue, host)`. The host name is
kept as the job record gives it; a bare name is distinct within its
queue and never compared across queues. When a landing has
fingerprinted the host, the node record links to that NodeEnvironment,
and the fingerprint joins the published exclusion so a carrier can
match on either.

## The detector

Every five minutes, over a sliding window of the terminal production
jobs (finished and failed) per queue, grouped by host. A node trips
when all of these hold in the window:

- at least `min_jobs` terminal jobs on the host;
- at least `failed_fraction` of them failed;
- at least `fast_fraction` of the failures were fast: shorter than
  `fast_ratio` of the queue's median finished walltime (the census's
  calibration), so a node that runs jobs to completion and then loses
  them is a different condition;
- the same tasks finish elsewhere on the queue in the window: at least
  one finished job of a task the host failed, on another host. This is
  the attribution: the failures follow the node, not the task and not
  the queue.

A node under the job floor is not judged. A node whose failures are
not fast, or whose tasks fail everywhere, is not a black hole; the
queue and task detectors own those.

The decision is a pure function over rows (`canary.guard.decide_nodes`)
with the thresholds passed in, so the same evidence gives the same
verdict anywhere and the thresholds are tested as data.

## The record

The node's state lives in the node map (the canary store), the queue
pattern repeated at node level: a `NodeState` per `(queue, host)` with
its status, the verdict that set it and its evidence (jobs, failed,
finished, fast failures, the tasks failed and where they finished,
first and last job in the window), the status change with its actor
(guard or manual), and an expiry. Status vocabulary: `clear`,
`black_hole`, `half_open`, `pinned` (a person's decision, not
overridden). A black hole expires after `expiry_h` into `half_open`:
one job may land; a clean outcome clears, a fast failure reopens for
twice the expiry. A person may clear or pin at any time.

Until the model exists, the guard runs stateless: every cycle judges
the window afresh, the cycle's state is the cached product
`node_guard_state`, and each node's change of verdict is an action.
Latching and expiry come with the model.

## Modes and settings

SysConfig keys, seeded at their defaults on first read so every knob is
visible on the System page:

| Key | Default | Meaning |
|---|---|---|
| `node_guard.enabled` | false | the global switch |
| `node_guard.mode` | shadow | `shadow`: decide, record and announce what the guard would do, act on nothing; `live`: exclude |
| `node_guard.queues` | [] | the queues judged; empty means every queue with production jobs in the window |
| `node_guard.window_h` | 2 | the sliding window |
| `node_guard.min_jobs` | 10 | the job floor per node |
| `node_guard.failed_fraction` | 0.8 | failed share of the node's terminal jobs |
| `node_guard.fast_fraction` | 0.5 | fast share of the node's failures |
| `node_guard.fast_ratio` | 0.25 | fast means shorter than this share of the queue's median finished walltime |
| `node_guard.expiry_h` | 24 | a black hole's life before half open |

In shadow mode a tripped node reads `would_exclude`; in live mode
`excluded`. Both are recorded the same way; only actuation differs.

## Actuation

Defensive and reversible, by what the production system controls:

- Where the production system owns the pilot launch (the Perlmutter
  launcher, the reference queue), the wrapper reads the published
  exclusion before the pilot fetches a job and exits on an excluded
  node, so no job is touched; the Perlmutter batch submission also
  excludes the node by name.
- On the OSG pools the lever that keeps pilots off a node is the
  Requirements clause of the submit description on the harvester
  submit host (swf-epicprod docs/OSG_SUBMISSION.md, Excluding what
  delivers nothing). The guard renders the clause from the record;
  applying it is an operation on that host, by PanDA operations.
- Everywhere, the payload's landing check declines in seconds on an
  excluded node with its own exit code (swf-epicprod
  docs/EPICPROD_PAYLOAD.md, the landing check). A decline costs the
  job one attempt, so this bounds the damage; it is not the guard.

The published exclusion is a JSON list of the current black holes
(queue, host, fingerprint when known, since, until, evidence), served
from the platform and mirrored to the public pilot prefix of the
devcloud bucket, where the wrapper and the landing check already
fetch.

## Readers and notices

- The Node guard page under PanDA on the monitor: every judged node
  with its state, reason and evidence, the queues' calibration, the
  switches, the cycle's time and errors; reads the cached product,
  computes nothing.
- The ePIC queues page: a node section per queue with the guard's
  current verdicts.
- Every change of a node's verdict is an action (`node_guard_decision`)
  on the action stream, one `node_guard_cycle` per cycle; a trip is a
  notice on the production notice stream and an alarm.

## Build order

1. This document; the pointer from CONTINUOUS_PRODUCTION.md's tripwire.
2. `canary.guard`: `normalize_host`, `decide_nodes`, dict tests.
3. The `node_guard_cycle` doer on the production-operations agent, the
   settings, the cached product, the decision and cycle actions, the
   cron enqueue; the Node guard page and its menu entry. Switched on in
   shadow mode.
4. The `NodeState` model and its migration in the canary store; latch,
   expiry and half open; the manual clear and pin; the queues page
   section.
5. The published exclusion; the wrapper check and the landing check;
   the rendered OSG clause and its handoff; live mode.
