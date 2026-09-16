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
jobs (finished and failed) per queue, grouped by host. Jobs that ended
under a declared downtime of their queue, or within the ten minutes
the pilot's cached queuedata lags a rule's expiration, are set aside
before judgment and counted on the cycle record and the page (the
platform's declared record from CRIC, swf-epicprod
CONTINUOUS_PRODUCTION.md, Declared downtime): a node is not a black
hole for a downtime's deaths. A node trips when all of these hold in
the window:

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

A second reading, after the standard one: the **fixed-time kill**. A
node that finished nothing in the window and whose failures all die
at the same time (the 90th percentile of their durations within
`fixed_time_ratio` of the 10th), from `fixed_min_jobs` failures up,
whatever their speed, is a black hole for the work it takes: a memory
ceiling, a slot that kills at its limit. The attribution is the same
(a task it failed finished on another node). The case that named it:
on 2026-09-14 the host voh5 behind BNL_OSG_PanDA_1 took seven jobs of
one task and killed every one at 40 minutes and 2.27 GB while the task
finished 4,996 jobs elsewhere at 2.8 GB; at 40 minutes against the
queue's 17-minute median the deaths were not fast, and seven was under
the floor.

A node under the job floor is judged for the fixed-time kill only and
listed only when it trips. A node whose failures are not fast and not
at one time, or whose tasks finish nowhere on the queue while other
nodes finish other work (`tasks_fail_everywhere`), is not a black
hole; the task detector owns that. Two conditions are the queue's, and
the guard names them rather than exclude for them:

- no other node of the queue finished anything in the attribution
  window (`no_other_node`): node and queue are one thing there, as on
  a cloud pool scaled to one node, and the queue's failure window is
  the instrument;
- more than `storm_nodes` hosts of one queue trip at once
  (`queue_event`): a storm, the queue's condition, handed to the queue
  breaker; the hosts are cleared with that reason and the queue reads
  `queue_event` with the count.
- a burst: more than `storm_nodes` distinct hosts of one queue failing
  inside one `storm_minutes` interval, whatever each host's count. A
  tripped host whose failures fall in the bursts (`failed_fraction` of
  them) is cleared with `queue_event`, its evidence marked `burst`;
  the queue reads `queue_event` and lists its bursts with their host
  counts. A black hole that kills for hours keeps tripping through a
  burst it did not cause, since most of its deaths lie outside it.
  The case that named it: on 2026-09-15 the input file of task 39973
  was refused by the JLab door for four minutes and 443 jobs died on
  412 hosts of BNL_OSG_PanDA_1, one or two each; the three Fir hosts
  with enough slots to pass the fixed-time floor tripped as black
  holes for the queue's event (segfault finding f-13). Under the
  tripped-host cap alone that storm read as three nodes.

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
| `node_guard.mode` | shadow | `shadow`: decide, record, announce and publish what the guard would do, the readers act on nothing; `live`: the published document excludes |
| `node_guard.queues` | [] | the queues judged; empty means every queue with production jobs in the window |
| `node_guard.window_h` | 4 | the judging window |
| `node_guard.attribution_h` | 24 | the look back for the tasks' finishes on other nodes |
| `node_guard.min_jobs` | 8 | the job floor per node |
| `node_guard.failed_fraction` | 0.8 | failed share of the node's terminal jobs |
| `node_guard.fast_fraction` | 0.5 | fast share of the node's failures |
| `node_guard.fast_ratio` | 0.5 | fast means shorter than this share of the queue's median finished walltime |
| `node_guard.storm_nodes` | 10 | more tripped hosts than this on one queue, or more hosts than this failing inside one `storm_minutes` interval, is the queue's event |
| `node_guard.storm_minutes` | 5 | the interval of the burst reading |
| `node_guard.fixed_min_jobs` | 5 | the failure floor of the fixed-time kill |
| `node_guard.fixed_time_ratio` | 1.2 | the failures' 90th over 10th percentile duration, at most, for a fixed-time kill |
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

Defensive and reversible, by what the production system controls.
Everything acts through one document, the published exclusion: every
cycle the guard is on, the record's black holes go out as JSON
(`queue`, `host`, `site`, `fingerprint` when known, `since`, `until`,
`reason`, `trips`) with the cycle's `mode` and a `valid_until` twenty
minutes on, stored as the cached product `node_guard_exclusion`, served
anonymously at `GET /api/node-guard/exclusion/` on the monitor, and put
on the devcloud bucket's public pilot prefix,
`https://epic-devcloud-stageout.s3.us-east-1.amazonaws.com/pilot/node-exclusion.json`
(swf-epicprod docs/DEVCLOUD_STAGEOUT.md § 4), with the worker profile
in the operating account's AWS credentials, since every worker can
fetch there and the monitor's faces are inside the perimeter or behind
a login. A reader acts only on a live document inside its validity, so
a publisher that stops leaves no exclusion standing within twenty
minutes; a shadow document is read and reported and never acted on,
which is how the chain is verified before live mode. A failed
publication is the cycle's error on the page and the cycle record.
Live since 2026-09-16.

The readers, in the order a job meets them:

- The wrapper, where the production system owns the pilot launch (the
  Perlmutter launcher, the npps0 pass script): reads the document
  before the pilot fetches a job and exits on an excluded node, so no
  job is touched. The OSG pilot wrapper on the harvester submit host is
  the same check, applied there as a separate operation with its dated
  backup (swf-epicprod docs/OSG_SUBMISSION.md); until it is, the OSG
  pool relies on the landing check.
- The payload's landing check, everywhere: declines in seconds on an
  excluded node with exit 80, the node, queue, reason and trip time in
  the stage log and the report (swf-epicprod docs/EPICPROD_PAYLOAD.md,
  evolution item 10). A decline costs the job one attempt, and the
  guard's expiry bounds how many.
- The Requirements clause of the OSG submit description (OSG_SUBMISSION
  .md, Excluding what delivers nothing) stays the operator's lever for
  a standing ban; the guard does not write it.

A host is matched by its full name, or by its bare name when the record
holds a bare name (the pool advertises both forms), and by queue when
the reader knows its queue; a bare-name collision across sites costs a
healthy node one declined attempt per job until the exclusion expires.

## Readers and notices

- The Node guard page under Sites on the monitor: every judged node
  with its site (the pilot-reported glidein site behind a pool queue,
  else the site its domain names), state, reason, first and last job,
  and its evidence under a triangle; the queues' calibration and any
  storm; the switches, the cycle's time and errors. Reads the cached
  product, computes nothing. The same reads as JSON for scripts at
  `panda/node-guard/json/` (the cycle's tripped nodes and the record's
  rows that are not clear), served anonymously on the monitor's http
  face.
- The ePIC queues page: a node section per queue with the guard's
  current verdicts.
- Every change of a node's verdict is an action (`node_guard_decision`)
  on the action stream, one `node_guard_cycle` per cycle; a trip is a
  notice on the production notice stream (`nodeguard.trip` from
  prod-notify, once per node, from the JSON above) and an alarm.

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
5. The published exclusion, the landing check, the wrapper check on
   the Perlmutter launcher and npps0, and live mode (done 2026-09-16);
   the OSG pilot wrapper's check, a separate operation on the submit
   host (open).
