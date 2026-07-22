from __future__ import annotations

import asyncio
import threading
from typing import Optional, Any

from mitmproxy import options
from mitmproxy.options import Options
from mitmproxy.tools.dump import DumpMaster

from mitmproxy_mcp.addon import CaptureAddon
from mitmproxy_mcp.flow_store import FlowStore


class ProxyManager:
    def __init__(self, store: FlowStore) -> None:
        self._store = store
        self._master: Optional[DumpMaster] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, port: int = 8080, listen_host: str = "127.0.0.1") -> str:
        if self.running:
            return "already running"

        self._thread = threading.Thread(
            target=self._run, args=(port, listen_host), daemon=True
        )
        self._thread.start()
        return f"started on {listen_host}:{port}"

    def stop(self) -> str:
        if not self.running:
            return "not running"

        if self._loop and self._master:
            self._loop.call_soon_threadsafe(self._master.shutdown)
        
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        
        return "stopped"

    def _run(self, port: int, listen_host: str) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        opts = Options(listen_host=listen_host, listen_port=port)
        self._master = DumpMaster(opts)

        # Addon registration
        self._master.addons.add(CaptureAddon(self._store))
        
        # Ensure Intercept addon is available for set_intercept tool
        from mitmproxy.addons import Intercept
        self._master.addons.add(Intercept())

        try:
            self._loop.run_until_complete(self._master.run())
        except Exception:
            pass
        finally:
            self._loop.close()

    def _schedule(self, coro: Any) -> str:
        if not self.running or self._loop is None or self._master is None:
            return "proxy not running"
        
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=5)
        except Exception as e:
            return f"error: {str(e)}"

    # Bridge methods

    def set_intercept(self, pattern: str) -> str:
        """Sets the intercept pattern. Empty pattern clears it."""
        async def _set():
            # mitmproxy Intercept addon uses an option 'intercept'
            # We need to update the options on the master
            self._master.options.update(intercept=pattern)
            return "intercept set"
        
        return self._schedule(_set())

    def resume_flow(self, flow_id: str) -> str:
        async def _resume():
            flow = self._store.get_flow_ref(flow_id)
            if not flow:
                raise ValueError(f"flow {flow_id} not found")
            if not flow.intercepted:
                raise ValueError(f"flow {flow_id} not intercepted")
            flow.resume()
            return "resumed"
        
        return self._schedule(_resume())

    def resume_all(self) -> str:
        async def _resume_all():
            flows = self._store.list_all()
            count = 0
            for f_dict in flows:
                # We need to find the actual flow objects to resume them
                # The store keeps the ref, but list_all returns dicts.
                # We need a way to get the refs for all intercepted flows.
                pass
            # Actually, it's easier to iterate the store's internal list if we expose it or use the ID
            # Let's assume we can iterate the store and find intercepted ones
            return "resume_all not implemented in detail yet"

        return self._schedule(_resume_all())

    def modify_request(self, flow_id: str, method: Optional[str] = None, path: Optional[str] = None, 
                       headers: Optional[dict] = None, body: Optional[str] = None) -> str:
        async def _modify():
            flow = self._store.get_flow_ref(flow_id)
            if not flow:
                raise ValueError(f"flow {flow_id} not found")
            if not flow.intercepted:
                raise ValueError(f"flow {flow_id} not intercepted")
            
            if method:
                flow.request.method = method
            if path:
                flow.request.path = path
            if headers:
                for k, v in headers.items():
                    flow.request.headers[k] = v
            if body is not None:
                flow.request.content = body.encode("utf-8", errors="replace")
            
            return "request modified"
        
        return self._schedule(_modify())

    def modify_response(self, flow_id: str, status_code: Optional[int] = None, 
                        headers: Optional[dict] = None, body: Optional[str] = None) -> str:
        async def _modify():
            flow = self._store.get_flow_ref(flow_id)
            if not flow:
                raise ValueError(f"flow {flow_id} not found")
            if not flow.response:
                raise ValueError(f"flow {flow_id} has no response")
            if not flow.intercepted:
                raise ValueError(f"flow {flow_id} not intercepted")
            
            resp = flow.response
            if status_code:
                resp.status_code = status_code
            if headers:
                for k, v in headers.items():
                    resp.headers[k] = v
            if body is not None:
                resp.content = body.encode("utf-8", errors="replace")
            
            return "response modified"
        
        return self._schedule(_modify())

    def drop_flow(self, flow_id: str) -> str:
        async def _drop():
            flow = self._store.get_flow_ref(flow_id)
            if not flow:
                raise ValueError(f"flow {flow_id} not found")
            flow.kill()
            return "dropped"
        
        return self._schedule(_drop())

    def replay_flow(self, flow_id: str, mode: str = "client") -> str:
        async def _replay():
            flow = self._store.get_flow_ref(flow_id)
            if not flow:
                raise ValueError(f"flow {flow_id} not found")
            
            cmd = "replay.client" if mode == "client" else "replay.server"
            # mitmproxy commands are called via master.commands.call(cmd, [args])
            await self._master.commands.call(cmd, [flow])
            return f"replayed ({mode})"
        
        return self._schedule(_replay())
