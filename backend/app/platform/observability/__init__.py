"""Platform overview observability: API performance monitoring.

Middleware records request timing for every HTTP route (except health,
MCP and SSE surfaces) into minute rollups; slow requests are persisted as
single evidence rows with per-layer breakdowns.  Admin query endpoints are
mounted under /api/v1/settings/monitoring.

The shared collector singleton lives at
app.platform.observability.collector.collector; this package deliberately
does not re-export it to keep the submodule importable by that name.
"""
