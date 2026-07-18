"""The canary state store: a packaged Django application.

Installable into a hosting Django runtime (first target: swf-monitor on
swfdb) in the snapper-ai pattern. The store carries the map spine —
sites, queues, node environments, landing reports — and grows tables as
the increments that feed them land. See docs/SWF_INTEGRATION.md for the
deployment contract.
"""
