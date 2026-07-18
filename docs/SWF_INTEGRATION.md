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
  and 5) run as standalone agent processes on the platform host,
  following the platform's prod-ops agent pattern.
- As a snapper-ai component owner, site-canary publishes its curated
  projections through the snapper-ai publication helper in the same
  runtime (PLAN.md increment 6).

## Open decisions

Named before first production writes, to be settled with the platform
deployment:

- ingress authentication and identity for landing deliveries, for
  probe jobs and for riders;
- site and queue naming authority — PanDA queue configuration as the
  source of canonical names;
- direct-database vs REST publication for platform-resident canary
  agents;
- retention policy for landing reports — the map spine is compact,
  the report stream accumulates;
- monitor page mounting and visibility.
