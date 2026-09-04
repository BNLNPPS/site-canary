# SWF integration

This document is the integration contract between the generic
site-canary store and its first deployment: the swf platform serving
ePIC. The design record is [DESIGN.md](DESIGN.md); the generic store is
described in [IMPLEMENTATION.md](IMPLEMENTATION.md).

## Integration boundary

site-canary owns probing, assessment, curation of queue-health state,
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

- Probe landing reports reach the store from PanDA job metadata: the
  pilot lifts the job's `jobReport.json` into the PanDA metatable, and
  the canary agent's dispatch cycle collects it through
  `CANARY_PANDA_DSN` (IMPLEMENTATION.md, Collection). The platform's
  authenticated REST ingress and file-staged collection remain the
  ladder's other rungs (DESIGN.md), for riders. Ingest deduplicates by
  node environment under horizontal identity.
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

The packaged app serves the canary page (`canary.store.views`,
templates under `canary/`): the site health table leading, probe
management below, at the `probes/` path; the canary root path
redirects to it. The health table is public read-only, matching the
System Status page; probe controls are direct-face writes. The
installation adds:

- `canary.store` to `INSTALLED_APPS`;
- `path('canary/', include('canary.store.urls'))` in the URL
  configuration;
- a `System` pulldown entry
  (`<a href="{% url 'canary:probes_page' %}">Canary probes</a>`) in the
  base template, with `active_nav` wiring;
- the canary queue-state fills (healthy, degraded, failing,
  insufficient, unknown), carried page-scoped until promoted to
  `state-colors.css`.

Page development outside the platform uses `scripts/webdev.py`, which
supplies stand-ins for the base template and `swf_fmt` filters
(`scripts/devweb/`); the hosted runtime never installs the stand-ins.

## Platform installation

The bootstrap sequence completed 2026-07-18; this section records the
standing installation on the swf-testbed host (`pandaserver02`).

- site-canary is installed editable with the `store` extra in the
  shared development venv, wired into `swf-testbed` (`install.sh`,
  `docs/installation.md`, `pyproject.toml`) beside snapper-ai. The
  swf-monitor deploy freezes it non-editable into each release venv,
  so production picks up canary changes only on deploy.
- The accounting query was verified against the live BNL instance:
  `doma_panda.jobsarchived4` matches the query as written, and the
  per-queue results reproduce the July 2026 profile in
  PANDA_USER_JOBS.md where the windows overlap.
- `canary.store` is in the monitor's `INSTALLED_APPS`; the `canary_*`
  tables live in `swfdb`. The canary page is mounted at
  `/canary/probes/` (the `/canary/` root redirects there), reached
  from the System and Sites pulldowns, with the health fills in
  `state-colors.css`. The queue list's canary cells link to per-queue
  probe run histories.
- **The canary agent** (`swf-monitor/agents/canary_agent.py`, namespace
  `canary` from `agents/canary.toml`) is the supervised singleton in
  the prod-ops pattern: systemd unit `canary-agent.service`
  (hand-installed in `/etc/systemd/system/`, `Restart=always`,
  deliberate-shutdown exit 100, `WorkingDirectory=/data/swf-tmp`: a
  stable path, since the deploy symlink resolves once at start and a
  later deploy would leave the process in a deleted release; the
  swf-monitor deploy script restarts the unit on every deploy),
  consuming `/queue/canary.ops`. Its
  `assess_refresh` handler runs `canary assess --panda --write` then
  `canary evaluate --write` as bounded subprocesses on the worker
  pool, and publishes a `canary_assess_complete` event to
  `/topic/epictopic`.
- Cadence: an hourly cron (minute 5) enqueues `assess_refresh` via
  `scripts/enqueue-ops-message.py --queue /queue/canary.ops
  --namespace canary`; the same command by hand is the on-demand
  trigger.
- Probes: an hourly cron (minute 20) enqueues `probe_dispatch` the
  same way; the agent's handler runs `canary probe-dispatch`, which
  collects the outcomes of open runs before submitting, and the
  page's Run now control enqueues the same message for one queue. The
  submission doer ships in the package (`canary/probe_kit/`) and runs
  from the deployed release: the sandbox vendors the canary wheel the
  deploy builds beside the release, the submission kernel and the
  production in-job runner are the release's scripts, and prmon is
  cached under the shared tree. A canary change reaches probes only
  through a full deploy. For a run by hand against another tree,
  `CANARY_PROBE_SUBMIT_CMD`, `CANARY_KIT_SOURCE`, `CANARY_DISPATCHER`,
  `CANARY_PRMON`, and `SWF_MONITOR_RELEASE` override. The probe's
  container is the current campaign's production image resolved from
  PCS through `SWF_MONITOR_URL` at dispatch (`CANARY_CONTAINER_IMAGE`
  overrides); the spec's `containerImage` is the fallback.
- Configuration: `CANARY_PANDA_DSN` and `CANARY_DB_*` (the swfdb
  store) in `/opt/swf-monitor/config/env/production.env` for the
  agent, and in `~/.env` for development use.
- The swf-remote face proxies the page at `/prod/canary/`
  (`remote_app` canary routes).
- The map holds its first real landing: site BNL, one node
  environment from `canary landing` on the platform host.

Development-only pieces never deploy: `scripts/devweb/`,
`scripts/webdev.py`, and the local development database.

## Next

PLAN.md increments 7–9: the AI surface (snapper-ai publication and
MCP tools), actuation and probes, and the rider.

## Open decisions

Named before first production writes, to be settled as the deployment
matures:

- ingress authentication and identity for landing deliveries, for
  probe jobs and for riders;
- issuance of the read-only PanDA accounting credential for the
  assessor — until issued, the monitor's `panda` account serves the
  read-only query;
- site and queue naming authority — PanDA queue configuration as the
  source of canonical names (the first landing was recorded under a
  manually chosen site name);
- direct-database vs REST publication for platform-resident canary
  agents — the assessor currently writes the store directly through
  the standalone settings;
- retention policy for landing reports — the map spine is compact,
  the report stream accumulates.
