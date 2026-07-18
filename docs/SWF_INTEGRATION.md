# SWF integration

This document is the integration contract between the generic
site-canary store and its first deployment: the swf platform serving
ePIC. The design record is [DESIGN.md](DESIGN.md); the generic store is
described in [IMPLEMENTATION.md](IMPLEMENTATION.md).

## Integration boundary

site-canary owns probing, assessment, curation of site-health state,
and the landscape map. The store (`canary.store`) installs as a Django
application in the swf-monitor runtime, in the same pattern as
snapper-ai and the installable epicprod applications: its models use
the monitor's default PostgreSQL connection and create `canary_*`
tables in `swfdb`. There is no second database, standalone web server,
or independent authentication stack. SWF owns route mounting, REST and
MCP authentication, environment configuration, release deployment, and
migration execution.

Store dependencies install with the `store` extra
(`pip install site-canary[store]`). The standalone harness
`scripts/storectl.py` configures Django around the store for
development and for deployments outside the platform; it is not part
of the hosted deployment.

## Writers

- Landing reports reach the store through the collection ladder
  (DESIGN.md): initially the platform's authenticated REST ingress;
  heartbeat-attached and file-staged collection follow in later
  increments. Ingest deduplicates by node environment under horizontal
  identity.
- The passive assessor and policy evaluator (PLAN.md increments 4
  and 6) run as standalone agent processes on the platform host,
  following the platform's prod-ops agent pattern. The assessor's live
  source reads PanDA accounting (`doma_panda.jobsarchived4`) through a
  read-only database identity supplied as `CANARY_PANDA_DSN`; its
  snapshot source carries the same rows as a file for development and
  relay.
- As a snapper-ai component owner, site-canary publishes its curated
  projections through the snapper-ai publication helper in the same
  runtime (PLAN.md increment 7).

## Canary page

The packaged app serves the Canary page (`canary.store.views`,
templates under `canary/`), mounted in the System pulldown of the
swf-monitor navigation. It is public read-only, matching the System
Status page. The installation adds:

- `canary.store` to `INSTALLED_APPS`;
- `path('canary/', include('canary.store.urls'))` in the URL
  configuration;
- a `System` pulldown entry
  (`<a href="{% url 'canary:canary_page' %}">Canary</a>`) in the base
  template, with `active_nav` wiring;
- the canary health fills (healthy, suspect, excluded, recovering,
  unknown), carried page-scoped until promoted to
  `state-colors.css`.

Page development outside the platform uses `scripts/webdev.py`, which
supplies stand-ins for the base template and `swf_fmt` filters
(`scripts/devweb/`); the hosted runtime never installs the stand-ins.

## Bootstrap sequence

The platform-side steps, on the swf-testbed host, in order:

1. Clone `BNLNPPS/site-canary` into the workspace beside the swf
   repositories and install it into the shared venv:
   `pip install -e '.[store]'`.
2. Verify the assessor's accounting query against the live BNL
   instance with a read-only `CANARY_PANDA_DSN`: run
   `canary assess --panda`, compare with the July 2026 profile in
   PANDA_USER_JOBS.md, and correct the query here if the live schema
   differs. Export a snapshot (`dump_snapshot`) for development use
   elsewhere.
3. Install the store into swf-monitor: `canary.store` in
   `INSTALLED_APPS`, migrate (`canary_*` tables in `swfdb`).
4. Mount the Canary page: URL include, System pulldown entry,
   `active_nav` wiring, canary health fills promoted to
   `state-colors.css`; smoke-check page content after deploy.
5. Stand up the assessor cadence as a supervised agent in the
   prod-ops pattern, writing passive samples on the configured
   interval.
6. First real landing: run `canary landing` on a BNL worker or
   interactive node, ingest it, and confirm the map starts with a
   real site.
7. Continue with PLAN.md increments 6–9 (policy, AI surface,
   actuation and probes, rider).

Development-only pieces never deploy: `scripts/devweb/`,
`scripts/webdev.py`, and the local development database.

## Open decisions

Named before first production writes, to be settled with the platform
deployment:

- ingress authentication and identity for landing deliveries, for
  probe jobs and for riders;
- issuance of the read-only PanDA accounting credential for the
  assessor, and verification of the accounting query against the live
  BNL instance;
- site and queue naming authority — PanDA queue configuration as the
  source of canonical names;
- direct-database vs REST publication for platform-resident canary
  agents;
- retention policy for landing reports — the map spine is compact,
  the report stream accumulates.
