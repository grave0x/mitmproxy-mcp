# mitmproxy-mcp — Specification & Implementation Plan (v3)

## 1. Concept

An MCP server that runs **mitmproxy** as a background process and exposes its HTTP traffic capture + manipulation capabilities to an AI host (Claude, etc.). The AI can inspect live flows, set intercept filters, modify requests/responses on the wire, replay, drop, and export flows — all through MCP tools and resources.

## 2. Architecture

```
┌──────────────┐   MCP (stdio)    ┌───────────────────┐   asyncio.run_coroutine_threadsafe  ┌────────────┐
│  AI Host     │◄────────────────►│  mitmproxy-mcp    │◄──────────────────────────────────►│  mitmproxy  │
│  (Claude)    │                  │  (MCP SDK FastMCP)│   (via ProxyManager)                │  (Master)   │
└──────────────┘                  └──────┬────────────┘                                     └────────────┘
                                         │
                                    ┌────▼────┐
                                    │FlowStore│
                                    │(thread- │
                                    │ safe)   │
                                    └─────────┘
```

- **mitmproxy** runs in a background daemon thread with its own asyncio event loop.
- **FlowStore** thread-safe container shared between addon (captures) and MCP server (serves to AI).
- **ProxyManager** bridges MCP thread ↔ mitmproxy thread via `asyncio.run_coroutine_threadsafe()`.
- **MCP Server** uses `mcp` SDK (FastMCP) over stdio transport, runs in main thread.

**CRITICAL THREADING RULE**: `flow.resume()`, `flow.kill()`, `flow.intercept()`, and `options.update()` must execute on mitmproxy's event loop. The `ProxyManager` provides methods that schedule these via `loop.call_soon_threadsafe()` / `asyncio.run_coroutine_threadsafe()`.

## 3. Modules

| Module | Responsibility |
|--------|---------------|
| `flow_store.py` | Thread-safe storage with UUID-based ID, dict serialization |
| `addon.py` | mitmproxy addon hooking `request`, `response`, `error`, TCP, DNS events |
| `proxy_manager.py` | Start/stop mitmproxy in daemon thread, bridge methods for flow control. Registers `Intercept` addon for intercept support. |
| `server.py` | FastMCP server definition: tools + resources + `main()` entry point |
| `__init__.py` + `__main__.py` | Entry points |

## 4. Tools

| Tool | Args | Returns | Description |
|------|------|---------|-------------|
| `start_proxy` | `port: int = 8080`, `listen_host: str = "127.0.0.1"` | Status string | Start mitmproxy on given port |
| `stop_proxy` | — | Status string | Gracefully stop mitmproxy |
| `proxy_status` | — | Status dict | Running status, port, flow count |
| `list_flows` | `filter_expr: str = ""` | list of dicts | List captured flows; pass-through mitmproxy filter syntax |
| `get_flow` | `flow_id: str` | dict | Full request + response details for one flow |
| `set_intercept` | `pattern: str` | Status string | Set intercept filter (empty clears it). Requires Intercept addon registered. |
| `resume_flow` | `flow_id: str` | Status string | Resume an intercepted flow. Scheduled on mitmproxy loop. |
| `resume_all` | — | Status string | Resume all intercepted flows. Scheduled on mitmproxy loop. |
| `modify_request` | `flow_id, method?, path?, headers?, body?` | Status string | Modify request on intercepted flow. Does NOT auto-resume. |
| `modify_response` | `flow_id, status_code?, headers?, body?` | Status string | Modify response on intercepted flow. Does NOT auto-resume. |
| `drop_flow` | `flow_id: str` | Status string | Kill/drop a flow |
| `replay_flow` | `flow_id`, `mode: str = "client"` | Status string | Client-side or server-side replay via `master.commands.call()` |
| `clear_flows` | — | Status string | Clear all captured flows |
| `export_flow` | `flow_id`, `format: str = "curl"` | Text string | Export flow as curl command or raw HTTP |

**Convention**: Tools that modify mitmproxy state use `asyncio.run_coroutine_threadsafe()` to schedule on the mitmproxy event loop. Tools that only read the store run directly on the MCP thread.

## 5. Resources

| URI | Returns | Description |
|-----|---------|-------------|
| `mitmproxy://flows` | list of dicts | Summary of all captured flows (id, method, host, path, status, intercepted) |
| `mitmproxy://flows/{flow_id}` | dict | Full flow detail (request + response) |
| `mitmproxy://status` | dict | Proxy running, port, flow count |

## 6. File-by-file Implementation Plan

### 6.1 `pyproject.toml`
```toml
[project]
name = "mitmproxy-mcp"
version = "0.1.0"
description = "MCP server for live viewing and interaction with mitmproxy"
requires-python = ">=3.11"
dependencies = [
    "mcp>=1.0.0",
    "mitmproxy>=11.0.0",
]

[project.scripts]
mitmproxy-mcp = "mitmproxy_mcp:main"

[tool.setuptools.packages.find]
where = ["src"]
```

### 6.2 `src/mitmproxy_mcp/flow_store.py`
- `StoredFlow` dataclass: id (UUID str), method, scheme, host, port, path, request_headers (dict), request_content (str|None), status_code (int|None), response_headers (dict|None), response_content (str|None), intercepted (bool), error (str|None), timestamp (float), content_type (str|None), is_replay (bool), flow_obj (strong ref to HTTPFlow)
- `FlowStore` class (thread-safe via `threading.Lock`):
  - `add(flow)` → generates UUID, stores strong ref to HTTPFlow, returns StoredFlow
  - `update(flow)` → finds by `id(flow)` in lookup dict, replaces stored data
  - `remove(flow_id)` → deletes
  - `get(flow_id)` → returns StoredFlow or None
  - `list_all()` → returns list copy
  - `clear()` → empties all
  - `max_flows` → if exceeded, FIFO evict oldest
- `stored_to_dict(sf)` → plain dict for JSON

### 6.3 `src/mitmproxy_mcp/addon.py`
- `CaptureAddon` class:
  - `__init__(self, store)` → stores ref
  - `request(self, flow)` → `store.add(flow)`
  - `response(self, flow)` → `store.update(flow)`
  - `error(self, flow)` → `store.update(flow)`
  - `tcp_message(self, flow)` → `store.add(flow)` for TCP streams
  - `dns_request(self, flow)` → `store.add(flow)` for DNS queries

### 6.4 `src/mitmproxy_mcp/proxy_manager.py`
- `ProxyManager` class:
  - `__init__(self, store)` → creates addon, no master yet
  - `start(port, listen_host)` → new event loop + daemon thread, builds Master, registers addons (including Intercept), runs
  - `stop()` → `loop.call_soon_threadsafe(master.shutdown)`, joins thread
  - `running` → property, thread.is_alive()
  
  **Bridge methods** (schedule on mitmproxy loop):
  - `set_intercept(pattern)` → `loop.call_soon_threadsafe(lambda: master.options.update(intercept=pattern))`
  - `resume_flow(flow_id)` → find stored flow via store, schedule `flow.resume()` on mitmproxy loop
  - `resume_all()` → iterate all intercepted from store, schedule resume each
  - `modify_request(flow_id, **kwargs)` → find stored, schedule modifications on flow obj (no auto-resume)
  - `modify_response(flow_id, **kwargs)` → find stored, schedule modifications on flow obj (no auto-resume)
  - `drop_flow(flow_id)` → find stored, schedule `flow.kill()`
  - `replay_flow(flow_id, mode)` → schedule `master.commands.call("replay.client", [flow])` or `replay.server`

  **Internal**:
  - `_run(port, listen_host)` → called in thread, creates Master, registers default_addons + Intercept + CaptureAddon, catches startup errors

### 6.5 `src/mitmproxy_mcp/server.py`
- Global `store` (FlowStore) and `proxy` (ProxyManager) instances
- FastMCP app with all tools + resources, returning native dicts/list
- `main()` calls `mcp.run(transport="stdio")`

### 6.6 `src/mitmproxy_mcp/__init__.py`
- Re-export `main` from `.server`

### 6.7 `src/mitmproxy_mcp/__main__.py`
- `from .server import main; main()`

## 7. Edge Cases & Error Handling

| Scenario | Behavior |
|----------|----------|
| Start proxy while already running | Return "already running" |
| Stop proxy when not running | Return "not running" |
| Flow ID not found | Return error string |
| Flow not intercepted but modify attempted | Return "flow not intercepted" |
| mitmproxy fails to start (port in use, etc.) | Catch exception in thread, return error to caller |
| Binary response content (> 1MB or non-UTF8) | Show `<binary: N bytes>` |
| No flows captured | Return `[]` |
| Flow store exceeds `max_flows` | FIFO eviction of oldest flows |
| Try to resume non-intercepted flow | Return "flow not intercepted" |
| mitmproxy thread crashes | `running` returns False, next tool call returns error |

## 8. Thread-Safety Strategy

```
MCP Thread (main)                    mitmproxy Thread (daemon)
┌────────────────────┐              ┌──────────────────────────┐
│  server.py         │              │  Master                  │
│  Tools:            │  run_coro_   │  Addons:                 │
│  • resume_flow()───┼─threadsafe──►│  • CaptureAddon          │
│  • modify_request()│              │  • Intercept             │
│  • drop_flow()     │              │  • default_addons()      │
│                    │              │                          │
│  Resources:        │  reads       │  FlowStore (Lock)        │
│  • list_flows()────┼─────────────►│  • add/update by addon   │
│  • get_flow()      │              │  • get/list by server    │
└────────────────────┘              └──────────────────────────┘
```

## 9. Verification

```bash
pip install -e .
mitmproxy-mcp  # starts MCP server on stdio
# Test via MCP inspector:
npx @modelcontextprotocol/inspector mitmproxy-mcp
```

## 10. To-dos

1. Create `pyproject.toml`
2. Write `flow_store.py`
3. Write `addon.py`
4. Write `proxy_manager.py`
5. Write `server.py` (tools + resources)
6. Write `__init__.py` + `__main__.py`
7. Install deps and smoke test
8. Run integration test with MCP inspector
