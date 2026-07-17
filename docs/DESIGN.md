# site-canary Design Considerations

site-canary tests the health and capability of the distributed computing
sites serving PanDA, actuates site exclusion and recovery through
native PanDA mechanisms, and maintains a live, evidence-based record of
site state and capability. It is written for ePIC and designed as an
experiment-agnostic PanDA tool. This document records the
considerations shaping the design and the decision record behind them.

## The role

Distributed production and analysis depend on knowing which sites are
healthy, which are not, and what each site can actually do. The
established reference for this role is ATLAS's HammerCloud:
continuous test jobs with real payloads at every site, automated
blacklisting and recovery feeding the workload manager, and site test
results as a shared operational record. ePIC needs the role from the
start of sustained production operations; site-canary provides it
in a small efficient package, and
extends it: with the rider mode below, every PanDA job is a potential
measurement instrument, and the product includes a live 
AI-consumable map of the
full processing resource landscape.

## The HammerCloud path

HammerCloud has served ATLAS for nearly two decades and its
design has proven itself at scale. Adoption for ePIC was studied in July
2026. Much of its considerable mass is
machinery that PanDA has since acquired natively.
Weighing the value added against the cost of adopting HC and its
dependencies, the decision was not to adopt it, and to develop
a lightweight system additive to PanDA's existing capabilities,
and designed as a modern component in the ePIC PanDA AI-enabled WFMS
ecosystem. 
Hammercloud's design has
valuable inheritance, and site-canary adopts central elements:

- Test jobs are real experiment payloads, not synthetic probes.
- Excluded sites continue to receive test jobs — exclusion applies to
  production, not to probes — which is what makes automated recovery
  work.
- The exclusion policy is compact and documented: HammerCloud's
  published policy amounts to tens of lines defining test classes and
  exclusion/recovery windows.

## What PanDA provides natively

The 2008-era gap HammerCloud filled is largely closed in modern PanDA,
which is what makes a compact implementation possible:

- **Queue status actuation.** PanDA queue status is honored by JEDI
  brokerage directly; acting on native status requires no custom
  brokerage hooks.
- **Accounting.** Job-start latency and success rates per queue are
  directly measurable from PanDA accounting data
  (see the queue responsiveness measurement in
  [PANDA_USER_JOBS.md](https://github.com/BNLNPPS/swf-epicprod/blob/main/docs/PANDA_USER_JOBS.md)).
- **Traffic segregation.** Global shares and gshare stamping separate
  probe traffic from production and analysis, in accounting and in
  dispatch.
- **Platform services.** The swf-monitor platform supplies the
  dashboards, alarms, action stream, and AI assessment machinery that
  HammerCloud had to build for itself.

## Design principles

1. **Passive first, active second.** Where real workload flows, health
   metrics come free from accounting: time-to-start, failure rates,
   throughput, computed on a cadence. Active probes have two jobs only:
   test queues with no current traffic, and answer capability questions
   real workload cannot (CVMFS resolution, container pulls, storage
   reads, GPU visibility). This inversion is the largest single economy
   over an always-probing model, and it keeps the probe compute bill
   negligible. The rider mode below extends passive observation to
   every node real work reaches.

2. **Time-to-verdict.** A probe that waits hours in an activated
   backlog measures nothing but the backlog. Probe jobs dispatch
   promptly through a small dedicated leaf in the global-shares tree,
   so a probe verdict reflects the site, not the queue depth. Prompt
   verdicts are what make early flagging of problematic sites
   possible: a problem surfaces on the canary's timescale, before
   production burns through it.

3. **Adaptive, event-driven cadence.** Healthy sites are probed
   sparsely. Suspicion — an alarm, a falling efficiency, an operator
   request — triggers probing; excluded sites receive recovery
   probes on a densening schedule. Fixed cadences are the fallback, not
   the model.

4. **Probes are checklists of capability checks.** Each probe performs
   a set of specific checks — CVMFS resolves, the container
   pulls, storage reads, the GPU is visible — each with its own
   recorded result, rather than one overall pass/fail. A failure names
   the capability that failed, and the passing checks accumulate into
   the live per-queue capability record: what runs where, as observed
   rather than as configured. This record is half the product.

5. **Actuation through native mechanisms.** Verdicts act on PanDA queue
   status, which brokerage honors. Probes continue at excluded sites,
   so recovery is automatic and evidence-based.

6. **Policy is compact, declared, and versioned.** The exclusion and
   recovery policy is a concise statement, versioned with the
   deployment that owns it: each experiment supplies its own. Manual
   state setting wins over probe verdicts.

7. **Rules decide, AI advises.** Site status changes affect production,
   so they are made by the deterministic loop: recorded evidence,
   declared policy, reproducible verdict — the reason for any exclusion
   can be stated exactly and rechecked. AI works on the products of the
   loop, where judgment helps: diagnosing why a site is failing,
   correlating anomalies across queues, and writing site-health
   narratives for periodic reports. AI informs and proposes; it does
   not change site status.

8. **Curated publication.** site-canary owns site-health facts and
   publishes them at declared resolution — health states and capability
   changes, not raw jittery pass rates. Its projections are suitable
   for state snapshotting alongside the other owner-curated projections
   of the platform.

9. **Provenance.** Every status change is an action-stream record
   carrying the probe evidence behind it, in the platform's
   observation, recommendation, approval, action classification.

## The rider mode

A canary rides every pilot. Alongside the dedicated probe jobs, a
rider embedded in the ePIC job carrier runs a quick local check
wherever a pilot lands, decides whether the landing merits data
gathering, and when it does, builds an information packet for
delivery to the collector. Real workload then maps the processing
landscape as a side effect of flowing through it: the map covers every
node work actually reaches, at marginal cost near zero, dynamic and
adapting as the landscape changes.

### The gather decision

The rider always computes a cheap fingerprint of its execution
environment: platform and kernel, CPU model and core count, memory,
container runtime, CVMFS revision and reachability, GPU presence and
driver, and similar bounded facts. It gathers fully on three triggers:
an environment not in the current map, a map entry past its freshness
horizon, or a fingerprint deviating from the mapped one. At steady
state the decision costs milliseconds and no network traffic; when a
site quietly changes (an OS upgrade, CVMFS failing on one rack), the
deviation trigger catches it on the next landing. The check comes
first and gathering only follows a positive decision: the rider adds
no material processing burden to the PanDA worker, whose always-on
cost is the fingerprint comparison alone.

### Trickle, not flood

The gather decision is what keeps O(100k) concurrent cores from
flooding the collector. Gathering is the exception: a mapped, fresh,
unchanged environment produces no packet at all. Packet volume follows
landscape change rather than workload volume, so traffic rises exactly
when the landscape changes, which is when the data is wanted. The
collector deduplicates on arrival, and downstream consumers see only
the curated projections, never per-pilot traffic. Where node identity
does not work at a site — environments too uniform or too obscured for
the fingerprint to distinguish — the fallback is random sampling: a
configured fraction of landings, perhaps of order 10%, gathers, keeping the
map current and the load bounded without identity.

### Identity is horizontal

Node identity is treated deliberately loosely. The map does not need
to track a specific node across time and remappings; it needs to
distinguish nodes within the current map, so that the census is right:
how many GPU nodes, how many high-memory nodes, how many distinct
platforms behind a queue. The fingerprint provides that horizontal
identity. The second requirement is detecting when the whole map
changes, visible as a shift in the fingerprint population behind a
queue. Longitudinal identity of individual hosts is not needed.

### The collection ladder

The packet finds a way to be collected. The preferred path attaches packets to the
pilot's periodic heartbeat and health messages, which flow to
the PanDA server on a roughly 15-minute cadence through existing,
authenticated plumbing. Where the node has outbound connectivity,
direct REST to the collector is available. As a fallback, packets
are staged as small output files, offering a simple if delayed collection
mechanism. The working path is learned per site and adapts to site behavior changes.

### Conduct at sites

The rider is a guest on every node it observes, and its good conduct is
part of the design. It is bounded: seconds of wall time, megabytes of
memory, no persistent processes, no probing beyond the standard
interfaces of its own execution environment. It is fail-silent: a
rider failure never fails a pilot or a payload, and is at most visible
as the rider's own absence from the map. And it is documented: the
packet schema is published in this repository, so a site administrator
can see exactly what is collected — bounded operational facts of the
execution environment — and what it costs. The rider's
resource budget is stated and enforced; nothing about its behavior
should register as resource misuse or draw an administrator's
concern.

### prmon

prmon, actively maintained in the HSF repository, is the measurement
tool this design leans on and follows. Probes use it to
characterize the resources they land on.
prmon could also be run on production payloads, making every
production job a resource measurement of the node it ran on. In that case
the prmon output has to be collected from every production job, which
adds collection machinery to production jobs. It is an option, and the
design does not depend on it.

### Probes and riders

Riders observe passively, wherever work flows.
Probes test, actively and deliberately, where observation is
insufficient: quiet queues, capability checks, exclusion recovery.
The rider ships with the ePIC job carrier. Probe jobs are where
rider changes can be exercised before they ride production work: the
canary tests the canary.

## Burn-through protection

A misbehaving site or node can burn through a task's jobs at speed:
failures in seconds to minutes, workers cycling fast, a retry budget consumed
before anyone looks. The burn-through signature — a failure-rate spike
with anomalously short job durations — is visible in the passive
stream and is a suspicion trigger of the highest priority: prompt
probing, verdict, exclusion on the canary's timescale, so the burn
stops while it is still small. Rider fingerprints attribute fast failures at
node level, distinguishing a black-hole node behind a healthy site
from a sick site; and the map distinguishes burn localized to a site,
answered by exclusion, from burn everywhere, indicating a task or
configuration problem answered by pausing the task. The job-carrier
platform role adds a further preventive option: a carrier that checks
its landing against known-bad fingerprints and declines the work.
Production-side recovery from burn-through (raised retry ceilings,
easy rerun) is WFMS scope; preventing and bounding the burn is
canary scope.

## AI-ready products

The products of site-canary — the health states, the capability
record, the landscape map, and their histories — are designed for
effective AI use: structured, self-describing, bounded, and
provenance-carrying, retrievable through the platform's MCP services
alongside its other operational state. They are tailored to and
integrated with the AI tools appropriate to them; the snapper-ai fit
below is the first such integration, carrying canary's projections
into the durable, AI-readable state history that the platform's
assessment and diagnostic AI consumes.

## AI proposals

Site whitelisting and blacklisting are determined by procedural
algorithms. AI monitoring of canary data will also be explored, with
the AI proposing actions it deems appropriate but which are not
algorithm triggered.
This is always through a human gated mechanism. When the AI finds something
to propose, it prepares a deterministic implementation, with an accept
button; clicking the button runs the deterministic action.
This is the first clear application of human-in-the-loop AI decision making.
More will be explored as use cases emerge.

## Fit with snapper-ai

[snapper-ai](https://github.com/BNLNPPS/snapper-ai) aggregates
subsystem-maintained current state into coherent, durable, AI-readable
history: each subsystem owns a bounded, curated projection published at
a declared resolution, and any canonical change in the projection is
worth recording. site-canary is a natural component owner in that
model. It maintains the authoritative current state of site health and
capability, and publishes it as a curated projection: health states,
capability records, and exclusion state with policy provenance. The fit
strengthens as the product grows beyond site on/off — capability
records, worker maps recording which workers serve which queues and
what their environments provide, and graded health are richer state
components under the same ownership and resolution discipline. The division of
work is clean: site-canary owns probing, assessment, and curation;
snapper-ai records the history and serves temporal retrieval.

## A development platform for the job-carrier layer

Probe jobs are small, frequent, expendable, and fully instrumented,
which makes site-canary a working development and test platform for the
job-carrier layer: refinement of the ePIC job carrier in the epicprod
and testbed contexts, and development and testing of tools in that
domain. prmon, actively maintained in the HSF repository, is the first
candidate: carried in probe payloads, it characterizes the resources
probes land on — memory, CPU, I/O — and its measurements feed the
capability record.

## Components

A standalone probe agent (scheduled execution, REST interfaces, its own
state store; no web-framework coupling), a probe payload set drawn from
real ePIC payloads, the policy file, actuation through PanDA queue
status, and monitor pages and action-stream records for everything it
observes and does. The core is experiment-agnostic: the probe and
rider framework, gather decision, collector, policy engine, and PanDA
actuation carry no ePIC specifics; an experiment deployment supplies
the payload set, the policy values, and the platform integration, with
ePIC as the first.

## Naming

The name follows the coal mine canary, the sentinel carried in by
every crew, which the rider mode makes literal. The site is the
operational unit, exclusion acts at site level, and testing a site means testing
all its parts. One alternative was considered and, with regret,
excluded: the oxpecker, the bird that rides large animals, surely
including pandas, and alarm-calls at approaching danger — the
precise zoological niche of the rider, in a menagerie anchored by the
panda. But, no.

## References

- [PANDA_USER_JOBS.md](https://github.com/BNLNPPS/swf-epicprod/blob/main/docs/PANDA_USER_JOBS.md)
  — global shares mechanism, BNL EIC instance state, and the queue
  responsiveness measurement (July 2026).
- [ePIC WFMS documentation](https://epic-wfms-docs.readthedocs.io/) —
  the platform site-canary builds on.
- [HammerCloud](https://hammercloud.cern.ch/) — the role's reference
  implementation, in ATLAS.
- [snapper-ai](https://github.com/BNLNPPS/snapper-ai) — owner-curated
  state history for the platform; site-canary is a prospective
  component owner.
- [prmon](https://github.com/HSF/prmon) — the HSF process monitor,
  first candidate tool for probe resource characterization.
