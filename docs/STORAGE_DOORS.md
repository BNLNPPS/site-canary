# Storage doors

The canary for storage infrastructure: the doors production writes
through are used, on a cadence, from one vantage, and what they answer
becomes a record production operations can act on while the damage is
still small. This document is the plan of record; the design record is
[DESIGN.md](DESIGN.md) and the node guard, whose cycle, publication and
readers this follows piece for piece, is [NODE_GUARD.md](NODE_GUARD.md).

## The problem it answers

The host certificate of the BNL-XRD write door,
`epicxrd1.sdcc.bnl.gov:1094`, expired on 2026-09-20 at 23:59:59 GMT and
was not renewed for two days. Every production job whose output RSE is
BNL-XRD had no way home: preserve first goes to that door and the
upload client's fallback goes to the same door. The jobs simulated and
reconstructed their events, validated the output, and died at
registration — 17,565 Perlmutter jobs on 9/21 and 1,760 more between
04:00 and 12:40 ET on 9/22, about twelve minutes of finished physics
each, delivering nothing. Nothing in the system used that door except
the jobs, so nothing knew it was dead until a person read a job log.

A door is storage infrastructure. It is not a property of a queue, and
every queue writing through it asks the same question, so the question
is asked once, centrally, rather than by each queue's probes.

## Not a job

The check does not run as a PanDA job. A job buys a worker's vantage,
batch scheduling and a PanDA record, and this check needs none of them;
it costs the queue wait that makes a probe's verdict late, and a door
check that cannot run because its queue is starved is circular. The
cycle runs on the production-operations agent, which holds the
credential and stands where the registrar writes from.

The one thing a job would add is the site's own reachability — whether
a Perlmutter node can reach the JLab door — and that is a site
question, answered by the probes that already run as jobs
(IMPLEMENTATION.md, Probes). This canary says the door is alive, not
that every site can reach it, and says so in its record.

## The cycle

On the production-operations agent, one door at a time, at a cadence
that is a setting rather than a cron line: the enqueue runs every
fifteen minutes and a door is probed when its last probe is older than
`interval_h`, which starts at one hour. Changing the cadence is then a
settings change on the System page, and a door added mid-interval is
probed on the next enqueue rather than at the next hour.

1. **The doors.** From the catalog, not a list in the code: each RSE's
   write protocol gives the door's scheme, host, port and prefix, so an
   RSE added to the catalog is checked the day it exists. A settings
   key names RSEs to skip.
2. **The probe.** One write of a small file to a fixed path under the
   RSE's prefix, one stat of what was written, one delete. One cycle,
   no retries inside it: a door that is down sees one touch an hour and
   never a storm, and the fixed path means a delete that fails is
   overwritten by the next cycle instead of accumulating.
3. **The certificate.** The date on the certificate the door serves,
   read in the same cycle, because it names the cause when the write
   fails and gives warning before it fails: a door is reported expiring
   inside `warn_days` while it still works.
4. **The verdict**, a pure function over the readings
   (`canary.doors.decide_doors`): `up` when the write and the stat
   succeeded; `down` when the write was refused, with the refusal and
   the certificate's date as its evidence; `unknown` when the probe
   could not be formed or gave no answer at all. Silence is never
   `down`. A delete that fails is recorded and reported and does not
   make a door `down`: the door took the bytes, which is the question
   production asks of it, and the fixed path bounds what a failed
   delete leaves behind.

   The decision reads every door of a cycle together, because the
   reading that looks like a dead door is also what a canary with a
   bad credential sees. When every probed door refuses in the same
   cycle with the same authorization class, none is `down`: they are
   `unknown` with the reason `canary_credential`, the canary's own
   condition and an error on the cycle. This is the node guard's
   `queue_failing` reading at storage level — the instrument does not
   condemn the world for its own fault.

## The record and its publication

No new table. The cycle leaves the cached product
`storage_door_state` (every door, its verdict, its evidence, the
certificate's date, the cycle's time), one `storage_door_cycle` action,
and a notice on a change of verdict, so a door that dies is on the
production notice stream within the hour rather than in a job log.

The verdicts are served anonymously at `GET /api/storage-doors/` on the
monitor, carrying the cycle's time and a `valid_until` so a reader can
tell a current reading from a stale one. The validity is three hours
against the node guard's twenty minutes, because the cadence is hourly:
a reading must outlive one missed cycle, and a door's state does not
change on the minute. It is a setting beside the cadence and is kept
above twice `interval_h`.

## Nothing acts on it automatically

The canary reports; production operations act. A door that trips raises
its notice and its alarm, and what follows — the certificate renewed,
the tasks held, the writes pointed elsewhere — is an operator's
decision on an operator's timescale (Torre, 2026-09-22). No automatic
actuation is wired to this record, and none is added without that
decision being taken deliberately: a wrong verdict here would stop
production everywhere at once, which is exactly the blast radius the
node guard's per-node scope avoids.

In particular the payload is not tied to it. The payload's landing
check reads the certificate of its own write door itself
(swf-epicprod docs/EPICPROD_PAYLOAD.md, exit code 86), which is a job's
own business and stands on its own evidence. When tying the two is
wanted, the first step is a shadow reader — the landing fetches the
record, reports what it would have done, declines nothing — exactly as
the node guard was proven before live mode.

The record's other readers are readers, not actuators: the registrar
and the assessments explain a stalled delivery against a door known to
be down rather than leaving it unexplained.

## Settings

SysConfig keys, seeded at their defaults on first read:

| Key | Default | Meaning |
|---|---|---|
| `storage_doors.enabled` | false | the global switch |
| `storage_doors.mode` | shadow | `shadow`: probe, record and report, nothing acts; `live` is reserved for an actuator that does not exist yet and is not set without the decision above |
| `storage_doors.interval_h` | 1 | a door is probed when its last probe is older than this |
| `storage_doors.skip_rses` | [] | RSEs not probed |
| `storage_doors.probe_prefix` | `/canary` | the path under the RSE's prefix the probe writes to |
| `storage_doors.timeout_s` | 60 | each of the write, the stat and the delete |
| `storage_doors.warn_days` | 7 | a certificate this close to its end is reported |
| `storage_doors.validity_h` | 3 | the published document's life |

## The load it presents

Per door per cycle: one write of about a kilobyte, one stat, one
delete, and one TLS handshake for the certificate. Hourly over the six
doors in the catalog that is 144 cycles a day, some 430 namespace
operations and under a megabyte of data. A single quiet production day
moves tens of thousands of files and tens of terabytes through the same
doors, so the canary is far under a percent of the namespace traffic
and nothing at all in bytes.

## Build order

1. This document.
2. `canary/doors.py`: the probe, the certificate read, `decide_door`,
   dict tests.
3. The `storage_door_cycle` doer on the production-operations agent,
   the settings, the cached product, the cycle action and the change
   notice, the hourly cron enqueue. Switched on in shadow mode.
4. The monitor endpoint and the page: the doors with their verdicts,
   their certificates and their last write, beside the node guard's.
5. Only on a deliberate decision, and shadow first: a reader. The
   published document on the devcloud public pilot prefix exists when
   something off the monitor needs to read it, and not before.
