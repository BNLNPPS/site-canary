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
- the same tasks finish elsewhere on the queue: at least one finished
  job of a task the host failed, on another host of the queue, over
  the attribution window (`attribution_h`, longer than the judging
  window, because a queue's other nodes need not finish inside the
  same few hours). This is the attribution: the failures follow the
  node, not the task and not the queue.

A node under the job floor is not judged. A node whose failures are
not fast, or whose tasks finish nowhere on the queue while other nodes
finish other work (`tasks_fail_everywhere`), is not a black hole; the
task detector owns that. Two conditions are the queue's, and the guard
names them rather than exclude for them:

- no other node of the queue finished anything in the attribution
  window (`no_other_node`): node and queue are one thing there, as on
  a cloud pool scaled to one node, and the queue's failure window is
  the instrument;
- more than `storm_nodes` hosts of one queue trip at once
  (`queue_event`): a storm, the queue's condition, handed to the queue
  breaker; the hosts are cleared with that reason and the queue reads
  `queue_event` with the count.

A host named in `not_nodes` is a submit host that a job dies on before
it lands (the harvester and the OSG submit host appear as the
modificationhost of such jobs); it is never judged as a node, and its
outcomes are reported beside the queue.

The decision is a pure function over rows (`canary.guard.decide_nodes`)
with the thresholds passed in, so the same evidence gives the same
verdict anywhere and the thresholds are tested as data.

## The record

The node's state lives in the node map (the canary store), the queue
pattern repeated at node level: a `NodeState` per `(queue, host)` with
its status, the verdict that set it and its evidence (the actual site,
jobs, failed, finished, fast failures, how long the failures ran, their
error codes, the tasks failed and where they finished, the most recent
failed jobs, first and last job in the window), the status change with
its actor
(guard or manual), and an expiry. Status vocabulary: `clear`,
`black_hole`, `half_open`, `pinned` (a person's decision, not
overridden). A black hole expires after `expiry_h` into `half_open`:
one job may land; a clean outcome clears, a fast failure reopens for
twice the expiry. A person may clear or pin at any time.

The record is `canary.store.nodes`: `transition` is the pure rule
(the node's current state and the cycle's reading in, the next state
and the change out; `tests/test_nodes.py`), `apply` runs it over the
store one node per transaction, `set_status` is a person's clear, pin
or hand-opened black hole (no expiry), `current_exclusion` the black
holes as a carrier needs them. Every cycle tells the record every
judged node's verdict and, for every held node the floor did not
reach, its window counts, so a latched or half-open node is revisited
each cycle. A clear node with no record gets none. Each change of the
record is a `node_guard_decision` action; the cycle's own reading of
every judged node is the cached product `node_guard_state`, and a
standing black hole is recorded hourly.

## Modes and settings

SysConfig keys, seeded at their defaults on first read so every knob is
visible on the System page:

| Key | Default | Meaning |
|---|---|---|
| `node_guard.enabled` | false | the global switch |
| `node_guard.mode` | shadow | `shadow`: decide, record and announce what the guard would do, act on nothing; `live`: exclude |
| `node_guard.queues` | [] | the queues judged; empty means every queue with production jobs in the window |
| `node_guard.window_h` | 4 | the judging window |
| `node_guard.attribution_h` | 24 | the look back for the tasks' finishes on other nodes |
| `node_guard.min_jobs` | 8 | the job floor per node |
| `node_guard.failed_fraction` | 0.8 | failed share of the node's terminal jobs |
| `node_guard.fast_fraction` | 0.5 | fast share of the node's failures |
| `node_guard.fast_ratio` | 0.5 | fast means shorter than this share of the queue's median finished walltime |
| `node_guard.storm_nodes` | 10 | more tripped hosts than this on one queue is the queue's event |
| `node_guard.not_nodes` | the harvester and OSG submit hosts | hosts that are not worker nodes |
| `node_guard.expiry_h` | 24 | a black hole's life before half open |

## What the backtest measured

The decision at these defaults, run over the job record in four-hour
windows stepped by thirty minutes with a 24-hour attribution look
back, against the crash record's node events (swf-epicprod
docs/SEGFAULT_DIAGNOSIS.md, the findings), 2026-09-14:

- f-9, five GREX nodes, 2026-08-04 to 08-07: n389, n390, n391 and
  n392 trip in the first window that holds the event's start and stay
  tripped through it (n389 peaks at 2,070 failures in one window); two
  further GREX nodes of the same days trip for shorter spells.
- f-7, the same nodes on 2026-08-10: n388 and n391 trip at 14:54.
- f-5, warlock12 at BNL_OSG_EPIC_PROD_1, 2026-08-16: trips from the
  first window, its two slots read as one host; two nodes of the
  exclusion list's ComputeCanada-Fir family trip beside it.
- f-8, the August storm at BNL_OSG_EPIC_PROD_1 (316 hosts in one
  window) and the Perlmutter storm of 2026-08-20 (123 hosts): read as
  the queue's event, no node tripped.
- f-6, one Google node on 2026-08-18: not tripped, `no_other_node`;
  the pool had one node that day, and the queue's failure window (139
  outcomes, 95% failed) is the instrument that trips.

The fast ratio is 0.5 because f-5's kills came at 35.5 minutes, 0.32
of the queue's median finished walltime, and f-9's at 14 to 16
minutes, 0.15; the attribution looks back a day because f-6's node
had no other node finishing in its hour.

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

- The Node guard page under Sites on the monitor: every judged node
  with its site (the pilot-reported glidein site behind a pool queue,
  else the site its domain names), state, reason, first and last job,
  and its evidence under a triangle; the queues' calibration and any
  storm; the switches, the cycle's time and errors. Reads the cached
  product, computes nothing.
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
   expiry and half open; the manual clear and pin (done 2026-09-14,
   canary migration 0008). The queues page section.
5. The published exclusion; the wrapper check and the landing check;
   the rendered OSG clause and its handoff; live mode.
