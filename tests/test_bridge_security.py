import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from bridge_server import BridgeServer


class Socket:
    def __init__(self, message, origin=None):
        self.message = message
        self.request = SimpleNamespace(headers={"Origin": origin} if origin else {})
        self.closed = None
        self.sent = []

    async def recv(self):
        return json.dumps(self.message)

    async def close(self, code, reason):
        self.closed = code

    async def send(self, message):
        self.sent.append(json.loads(message))


def test_server_requires_strong_pairing_token(monkeypatch):
    monkeypatch.delenv("XHS_BRIDGE_TOKEN", raising=False)
    with pytest.raises(ValueError):
        BridgeServer()


@pytest.mark.parametrize(
    "token,origin",
    [
        ("", None),
        ("bad", None),
        ("x" * 32, "https://evil.example"),
    ],
)
def test_unauthorized_peers_cannot_reach_commands(token, origin):
    server = BridgeServer("x" * 32)
    socket = Socket({"role": "cli", "method": "ping_server", "token": token}, origin)
    asyncio.run(server.handle(socket))
    assert socket.closed == 1008
    assert not socket.sent


def test_authorized_status_and_no_secret_forwarding():
    server = BridgeServer("x" * 32)
    socket = Socket({"role": "cli", "method": "ping_server", "token": "x" * 32})
    asyncio.run(server.handle(socket))
    assert socket.sent == [{"result": {"extension_connected": False}}]


def test_malformed_handshake_is_rejected():
    socket = Socket([])
    asyncio.run(BridgeServer("x" * 32).handle(socket))
    assert socket.closed == 1008


def test_real_websocket_pairing_and_request_round_trip():
    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve
    from websockets.exceptions import ConnectionClosedError

    async def scenario():
        bridge = BridgeServer("x" * 32)
        async with serve(bridge.handle, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}"
            async with connect(url) as bad:
                await bad.send(json.dumps({"role": "cli", "token": "wrong"}))
                with pytest.raises(ConnectionClosedError):
                    await bad.recv()
            async with connect(url) as extension:
                await extension.send(json.dumps({"role": "extension", "token": "x" * 32}))
                assert json.loads(await extension.recv())["ready"] is True
                async with connect(url) as client:
                    await client.send(
                        json.dumps(
                            {
                                "role": "cli",
                                "token": "x" * 32,
                                "method": "test_read",
                            }
                        )
                    )
                    command = json.loads(await extension.recv())
                    assert "token" not in command
                    await extension.send(json.dumps({"id": command["id"], "result": "ok"}))
                    result = json.loads(await client.recv())
                    assert result["result"] == "ok"
                    assert not bridge._pending

    asyncio.run(scenario())
