# Storage doors

The canary for storage infrastructure: the doors production writes
through are used, on a cadence, from one vantage, and what they answer
is published where a job can read it before it does any work. This
document is the plan of record; the design record is
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
   (`canary.doors.decide_door`): `up` when the write, the stat and the
   delete all succeeded; `down` when the write was refused, with the
   refusal and the certificate's date as its evidence; `unknown` when
   the probe could not be formed or gave no answer at all. Silence is
   never `down`.

## The record and its publication

No new table. The cycle leaves the cached product
`storage_door_state` (every door, its verdict, its evidence, the
certificate's date, the cycle's time), one `storage_door_cycle` action,
and a notice on a change of verdict, so a door that dies is on the
production notice stream within the hour rather than in a job log.

Actuation is the node guard's, in form and in mechanism: every cycle
the verdicts go out as JSON with the cycle's `mode` and a
`valid_until`, stored as the cached product, served anonymously at
`GET /api/storage-doors/` on the monitor, and put on the devcloud
bucket's public pilot prefix,
`https://epic-devcloud-stageout.s3.us-east-1.amazonaws.com/pilot/storage-doors.json`,
where every worker can fetch it without a credential. A reader acts
only on a live document inside its validity, so a publisher that stops
leaves no verdict standing; a shadow document is read, reported and
never acted on.

The validity is three hours against the node guard's twenty minutes,
because the cadence is hourly: a document must outlive one missed
cycle, and a door's state does not change on the minute. It is a
setting beside the cadence and is kept above twice `interval_h`; a
cadence raised without it would leave readers acting on nothing for
the difference.

## The readers

- **The payload's landing check**, everywhere: it already fetches the
  node guard's exclusion, and fetches this beside it. When the door
  this job must write through is `down` in a live document inside its
  validity, the payload declines the landing with exit 86 in the first
  seconds (swf-epicprod docs/EPICPROD_PAYLOAD.md, exit code 86).
- **The payload's own certificate read** stays as the fallback between
  cycles: the canary speaks hourly, and a door that dies at 09:05 is
  read by the job itself until 10:00. The canary's write is the
  authority — it fails for reasons a certificate date cannot show — and
  the in-job read is the cheap sentinel that covers the gap.
- **The registrar and the assessments** read the record for what they
  already report: a delivery that stalls against a door known to be
  down is not an unexplained stall.

## Settings

SysConfig keys, seeded at their defaults on first read:

| Key | Default | Meaning |
|---|---|---|
| `storage_doors.enabled` | false | the global switch |
| `storage_doors.mode` | shadow | `shadow`: probe, record, publish, readers act on nothing; `live`: a `down` door declines landings |
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
4. The published document, the monitor endpoint, and the landing
   check's reader; live mode.
5. The page: the doors with their verdicts and certificates, beside the
   node guard's.
