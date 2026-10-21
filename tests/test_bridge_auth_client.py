import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from bridge_server import BridgeServer

from xhs.bridge import BridgePage
from xhs.bridge_auth import Session, client_challenge, client_hello, proof
from xhs.errors import CDPError
from xhs.request_state import RequestState

TOKEN = "test-pairing-token-" * 3


def test_sync_client_authenticates_real_server(monkeypatch):
    monkeypatch.setenv("XHS_BRIDGE_TOKEN", TOKEN)

    async def scenario():
        bridge = BridgeServer(TOKEN)
        async with serve(bridge.handle, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            page = BridgePage(f"ws://127.0.0.1:{port}")
            assert await asyncio.to_thread(page.is_server_running)
            assert not await asyncio.to_thread(page.is_extension_connected)

    asyncio.run(scenario())


def test_timeout_retry_after_client_restart_reuses_persisted_id(monkeypatch, tmp_path):
    monkeypatch.setenv("XHS_BRIDGE_TOKEN", TOKEN)
    monkeypatch.setenv("XHS_REQUEST_STATE", str(tmp_path / "requests.sqlite3"))

    async def scenario():
        bridge = BridgeServer(TOKEN, request_timeout=0.02)
        async with serve(bridge.handle, "127.0.0.1", 0) as server:
            url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            async with connect(url) as extension:
                hello = client_hello("extension")
                await extension.send(json.dumps(hello))
                response, es = client_challenge(TOKEN, hello, json.loads(await extension.recv()))
                await extension.send(json.dumps(response))
                assert es.unpack(await extension.recv())["ready"]
                first = asyncio.create_task(
                    asyncio.to_thread(
                        BridgePage(url)._call, "state_change", {"value": "private-input"}
                    )
                )
                command = es.unpack(await extension.recv())
                with pytest.raises(CDPError, match=command["id"]):
                    await first
                await extension.send(es.pack({"id": command["id"], "result": "done"}))
                # A new client has no in-memory state; its durable ID must recover the old result.
                assert (
                    await asyncio.to_thread(
                        BridgePage(url)._call, "state_change", {"value": "private-input"}
                    )
                    == "done"
                )
                assert len(bridge._requests) == 1
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(extension.recv(), 0.01)
        data = (tmp_path / "requests.sqlite3").read_bytes()
        assert TOKEN.encode() not in data and b"private-input" not in data

    asyncio.run(scenario())


def test_request_state_keeps_uncertain_id_until_matching_completion(monkeypatch, tmp_path):
    monkeypatch.setenv("XHS_REQUEST_STATE", str(tmp_path / "requests.sqlite3"))
    first, restarted = RequestState(), RequestState()
    assert first.obtain("hash", "original") == "original"
    assert restarted.obtain("hash", "new") == "original"
    restarted.complete("hash", "wrong")
    assert first.obtain("hash", "new") == "original"
    first.complete("hash", "original")
    assert restarted.obtain("hash", "new") == "new"


@pytest.mark.skipif(
    not shutil.which("node"), reason="Node is required for Python/extension interop"
)
def test_javascript_auth_matches_python_and_rejects_rogue_challenge():
    hello = client_hello("extension")
    server_nonce, server_id = "a" * 64, "b" * 64
    parts = (hello["role"], hello["client_nonce"], server_nonce, server_id)
    challenge = {
        "server_nonce": server_nonce,
        "server_id": server_id,
        "proof": proof(TOKEN, "server", *parts),
    }
    server = Session(proof(TOKEN, "session", *parts), "server")
    payload = {
        "token": TOKEN,
        "hello": hello,
        "challenge": challenge,
        "frame": server.pack({"ready": True}),
    }
    js = """
const fs = require('node:fs');
globalThis.crypto = require('node:crypto').webcrypto;
require(process.argv[1]);
const p = JSON.parse(fs.readFileSync(0, 'utf8'));
(async () => {
  const result = await BridgeAuth.challenge(p.token, p.hello, p.challenge);
  const ready = await result.session.unpack(p.frame);
  const response = await result.session.pack({ method: 'read' });
  let rejected = false;
  try { await BridgeAuth.challenge(p.token, p.hello, {...p.challenge, proof:'0'.repeat(64)}); }
  catch { rejected = true; }
  console.log(JSON.stringify({proof:result.response.proof,ready,response,rejected}));
})().catch(error => { console.error(error); process.exit(1); });
"""
    helper = Path(__file__).resolve().parents[1] / "extension/bridge-auth.js"
    run = subprocess.run(
        ["node", "-e", js, str(helper)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
    )
    result = json.loads(run.stdout)
    assert result["proof"] == proof(TOKEN, "client", *parts)
    assert result["ready"] == {"ready": True} and result["rejected"]
    assert server.unpack(result["response"]) == {"method": "read"}
