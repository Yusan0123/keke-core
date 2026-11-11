import importlib.util
import json
import pathlib
import sys
import unittest
from unittest.mock import patch

import httpx
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

PROJECT = pathlib.Path(__file__).resolve().parents[1]
RENDER = PROJECT.parent if (PROJECT.parent / "main.py").is_file() else PROJECT / "render"
sys.path.insert(0, str(RENDER))
spec = importlib.util.spec_from_file_location("museum_render_main", RENDER / "main.py")
main = importlib.util.module_from_spec(spec)
with patch("fastapi.staticfiles.StaticFiles", lambda **kwargs: StaticFiles(directory=str(pathlib.Path(__file__).parent))):
    spec.loader.exec_module(main)
memory = sys.modules["memory"]
ORIGINAL_ASYNC_CLIENT = httpx.AsyncClient
KEY = "local-validation-key"


class RenderProxyTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.failure = False
        main.MEMORY_API_TOKEN = KEY
        memory.MEMORY_API_TOKEN = KEY
        self.client = TestClient(main.app)

        def handler(request):
            self.calls.append(request)
            self.assertEqual(request.headers["X-Memory-Token"], KEY)
            if self.failure:
                return httpx.Response(503, json={"detail": "Cloudflare temporarily unavailable"})
            if request.url.path == "/memory/search":
                return httpx.Response(200, json={"results": [{"id": "saved-memory", "content": "local test record", "timestamp": "2026-11-08 21:33", "source": "来自MCP客户端", "score": 0.9}]})
            if request.url.path == "/pending":
                return httpx.Response(200, json=[{"id": "abcd-full-id", "content": "local pending test", "timestamp": "2026-11-08 21:33"}])
            if request.url.path == "/memory/approve_all":
                return httpx.Response(200, json={"ok": True, "total": 4})
            return httpx.Response(200, json={"ok": True})

        def client_factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return ORIGINAL_ASYNC_CLIENT(*args, **kwargs)

        self.transport = patch("memory.httpx.AsyncClient", client_factory)
        self.transport.start()

    def tearDown(self):
        self.transport.stop()
        self.client.close()

    def test_search_keeps_original_source_and_date(self):
        response = self.client.get("/memory/search", params={"q": "anniversary"}, headers={"X-Memory-Token": KEY})
        self.assertEqual(response.status_code, 200)
        record = response.json()["results"][0]
        self.assertEqual(record["timestamp"], "2026-11-08 21:33")
        self.assertEqual(record["source"], "来自MCP客户端")
        self.assertEqual(self.calls[0].url.host, "lumi-memory-museum.yusan-159357.workers.dev")

    def test_backend_failure_remains_an_error(self):
        self.failure = True
        response = self.client.get("/memory/search", params={"q": "anniversary"}, headers={"X-Memory-Token": KEY})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "Cloudflare temporarily unavailable")

    def test_mcp_save_keeps_mcp_provenance(self):
        response = self.client.post("/mcp", headers={"Authorization": "Bearer " + KEY}, json={
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": "museum_save", "arguments": {"content": "local test record"}},
        })
        self.assertEqual(response.status_code, 200)
        body = json.loads(self.calls[0].content)
        self.assertEqual(body["trigger"], "来自MCP客户端")
        self.assertEqual(body["content"], "local test record")

    def test_pending_diary_awaits_cloudflare(self):
        main._sessions.add("local-validation-session")
        self.client.cookies.set("session", "local-validation-session")
        response = self.client.get("/pending")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["id"], "abcd-full-id")

    def test_pending_short_ids_still_resolve(self):
        response = self.client.post("/memory/delete_pending", headers={"X-Memory-Token": KEY}, json={"id": "abcd"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(self.calls[-1].content)["id"], "abcd-full-id")

    def test_bulk_approval_state_is_delegated_to_cloudflare(self):
        response = self.client.post("/memory/approve_all", headers={"X-Memory-Token": KEY})
        self.assertEqual(response.json(), {"ok": True, "total": 4})
        self.assertEqual(self.calls[0].url.path, "/memory/approve_all")


if __name__ == "__main__":
    unittest.main()
