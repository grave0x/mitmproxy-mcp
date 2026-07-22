from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from threading import Lock
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mitmproxy.http import HTTPFlow


_MAX_FLOWS = 500


@dataclass
class StoredFlow:
    id: str = ""
    method: str = ""
    scheme: str = ""
    host: str = ""
    port: int = 0
    path: str = ""
    request_headers: dict = field(default_factory=dict)
    request_content: Optional[str] = None
    status_code: Optional[int] = None
    response_headers: Optional[dict] = None
    response_content: Optional[str] = None
    intercepted: bool = False
    error: Optional[str] = None
    timestamp: float = 0.0
    content_type: Optional[str] = None
    is_replay: bool = False


def _extract(flow: "HTTPFlow") -> StoredFlow:
    req = flow.request
    sf = StoredFlow(
        id=str(uuid.uuid4()),
        method=req.method,
        scheme=req.scheme,
        host=req.host,
        port=req.port,
        path=req.path,
        request_headers=dict(req.headers),
    )
    try:
        if req.content:
            sf.request_content = req.content.decode("utf-8", errors="replace")
    except Exception:
        sf.request_content = f"<binary: {len(req.content) if req.content else 0} bytes>"

    if flow.response:
        resp = flow.response
        sf.status_code = resp.status_code
        sf.response_headers = dict(resp.headers)
        ct = resp.headers.get("content-type", "")
        sf.content_type = ct
        if resp.content:
            if ct and "text" in ct:
                sf.response_content = resp.content.decode("utf-8", errors="replace")
            elif len(resp.content) < 1_048_576:
                try:
                    sf.response_content = resp.content.decode("utf-8", errors="replace")
                except Exception:
                    sf.response_content = f"<binary: {len(resp.content)} bytes>"
            else:
                sf.response_content = f"<binary: {len(resp.content)} bytes>"
    if flow.error:
        sf.error = flow.error.msg

    sf.intercepted = flow.intercepted
    sf.is_replay = flow.is_replay
    sf.timestamp = flow.timestamp_start or 0
    return sf


def stored_to_dict(sf: StoredFlow) -> dict:
    return asdict(sf)


class FlowStore:
    def __init__(self, max_flows: int = _MAX_FLOWS) -> None:
        self._max = max_flows
        self._lock = Lock()
        self._by_id: dict[str, StoredFlow] = {}
        self._by_flow_id: dict[int, str] = {}
        self._flow_refs: dict[str, "HTTPFlow"] = {}
        self._order: list[str] = []

    def add(self, flow: "HTTPFlow") -> str:
        sf = _extract(flow)
        with self._lock:
            self._by_id[sf.id] = sf
            self._by_flow_id[id(flow)] = sf.id
            self._flow_refs[sf.id] = flow
            self._order.append(sf.id)
            self._evict()
        return sf.id

    def update(self, flow: "HTTPFlow") -> Optional[str]:
        with self._lock:
            sf_id = self._by_flow_id.get(id(flow))
            if sf_id is None:
                return None
            sf = _extract(flow)
            sf.id = sf_id
            self._by_id[sf_id] = sf
            self._flow_refs[sf_id] = flow
        return sf_id

    def remove(self, flow_id: str) -> bool:
        with self._lock:
            if flow_id not in self._by_id:
                return False
            self._remove_unlocked(flow_id)
        return True

    def get(self, flow_id: str) -> Optional[dict]:
        with self._lock:
            sf = self._by_id.get(flow_id)
            if sf is None:
                return None
            return stored_to_dict(sf)

    def get_flow_ref(self, flow_id: str) -> Optional["HTTPFlow"]:
        with self._lock:
            return self._flow_refs.get(flow_id)

    def list_all(self) -> list[dict]:
        with self._lock:
            return [stored_to_dict(self._by_id[fid]) for fid in self._order if fid in self._by_id]

    def clear(self) -> None:
        with self._lock:
            self._by_id.clear()
            self._by_flow_id.clear()
            self._flow_refs.clear()
            self._order.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._order)

    def _evict(self) -> None:
        while len(self._order) > self._max:
            oldest = self._order.pop(0)
            self._by_id.pop(oldest, None)
            self._flow_refs.pop(oldest, None)

    def _remove_unlocked(self, flow_id: str) -> None:
        self._by_id.pop(flow_id, None)
        self._flow_refs.pop(flow_id, None)
        try:
            self._order.remove(flow_id)
        except ValueError:
            pass
