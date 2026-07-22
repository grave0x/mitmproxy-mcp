from __future__ import annotations

from typing import TYPE_CHECKING

from mitmproxy import ctx, http

if TYPE_CHECKING:
    from mitmproxy_mcp.flow_store import FlowStore


class CaptureAddon:
    def __init__(self, store: FlowStore) -> None:
        self._store = store

    def request(self, flow: http.HTTPFlow) -> None:
        if flow in ctx.master.addons:
            pass
        self._store.add(flow)

    def response(self, flow: http.HTTPFlow) -> None:
        self._store.update(flow)

    def error(self, flow: http.HTTPFlow) -> None:
        self._store.update(flow)
