import asyncio
import json
import secrets
import sys
from pathlib import Path

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed, ConnectionClosedError, InvalidStatus

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from bridge_server import BridgeServer

from xhs.bridge_auth import Session, client_challenge, client_hello

TOKEN = "test-pairing-secret-" * 3


async def authenticate(ws, role="cli", token=TOKEN):
    hello = client_hello(role)
    await ws.send(json.dumps(hello))
    challenge = json.loads(await ws.recv())
    response, session = client_challenge(token, hello, challenge)
    assert token not in json.dumps([hello, challenge, response])
    await ws.send(json.dumps(response))
    ready = session.unpack(await ws.recv())
    assert ready["ready"]
    return session


def host(bridge):
    return serve(bridge.handle, "127.0.0.1", 0, process_request=bridge.process_request)


def url(server):
    return f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"


def request_id(bridge):
    return f"{bridge.server_id}:{secrets.token_hex(16)}"


async def ask(ws, session, message):
    await ws.send(session.pack(message))
    return session.unpack(await ws.recv())


def test_server_requires_strong_pairing_token(monkeypatch):
    monkeypatch.delenv("XHS_BRIDGE_TOKEN", raising=False)
    with pytest.raises(ValueError):
        BridgeServer()


def test_mutual_authentication_and_authenticated_request_round_trip():
    async def scenario():
        bridge = BridgeServer(TOKEN)
        async with host(bridge) as server, connect(url(server)) as extension:
            es = await authenticate(extension, "extension")
            assert bridge._extension_ws is not None
            async with connect(url(server)) as client:
                cs = await authenticate(client)
                status = await ask(client, cs, {"method": "ping_server"})
                assert status["result"]["extension_connected"]
            async with connect(url(server)) as client:
                cs = await authenticate(client)
                await client.send(cs.pack({"method": "read", "request_id": request_id(bridge)}))
                command = es.unpack(await extension.recv())
                assert "token" not in command and "proof" not in command
                await extension.send(es.pack({"id": command["id"], "result": "ok"}))
                assert cs.unpack(await client.recv())["result"] == "ok"
                assert not bridge._pending

    asyncio.run(scenario())


def test_wrong_secret_and_forged_server_are_rejected():
    hello = client_hello()
    with pytest.raises(ValueError):
        client_challenge(
            TOKEN, hello, {"server_nonce": "a" * 64, "server_id": "b" * 64, "proof": "0" * 64}
        )

    async def scenario():
        bridge = BridgeServer(TOKEN)
        async with host(bridge) as server, connect(url(server)) as client:
            await client.send(json.dumps(client_hello()))
            await client.recv()
            await client.send(json.dumps({"proof": "wrong"}))
            with pytest.raises(ConnectionClosedError):
                await client.recv()

    asyncio.run(scenario())


def test_frames_reject_modification_replay_and_wrong_direction():
    sender, receiver = Session("key", "client"), Session("key", "server")
    raw = sender.pack({"method": "read"})
    assert receiver.unpack(raw)["method"] == "read"
    with pytest.raises(ValueError):
        receiver.unpack(raw)
    altered = json.loads(sender.pack({"method": "read"}))
    altered["body"] = altered["body"].replace("read", "write")
    with pytest.raises(ValueError):
        receiver.unpack(json.dumps(altered))
    with pytest.raises(ValueError):
        sender.unpack(sender.pack({"method": "read"}))


def test_web_origin_rejected_before_handshake_and_idle_peers_are_bounded():
    async def scenario():
        bridge = BridgeServer(TOKEN, max_unauthenticated=1, handshake_timeout=0.1)
        async with host(bridge) as server:
            with pytest.raises(InvalidStatus):
                async with connect(url(server), origin="https://evil.example"):
                    pytest.fail("origin accepted")
            async with connect(url(server)) as idle:
                # HTTP processing runs before starting the second handler.
                for _ in range(20):
                    if bridge._authenticating:
                        break
                    await asyncio.sleep(0.001)
                assert bridge._authenticating == 1
                with pytest.raises(InvalidStatus):
                    async with connect(url(server)):
                        pytest.fail("idle connection limit exceeded")
                with pytest.raises(ConnectionClosedError):
                    await idle.recv()
            for _ in range(50):
                if bridge._authenticating == 0:
                    break
                await asyncio.sleep(0.001)
            assert bridge._authenticating == 0

    asyncio.run(scenario())


def test_duplicate_extension_never_replaces_owner():
    async def scenario():
        bridge = BridgeServer(TOKEN)
        async with host(bridge) as server, connect(url(server)) as first:
            await authenticate(first, "extension")
            owner = bridge._extension_ws
            async with connect(url(server)) as second:
                with pytest.raises(ConnectionClosedError):
                    await authenticate(second, "extension")
            assert bridge._extension_ws is owner
            async with connect(url(server)) as client:
                cs = await authenticate(client)
                assert (await ask(client, cs, {"method": "ping_server"}))["result"][
                    "extension_connected"
                ]

    asyncio.run(scenario())


def test_timeout_retries_join_original_operation_and_return_late_result():
    async def scenario():
        retry_joined = asyncio.Event()

        class ObservedBridge(BridgeServer):
            async def _handle_cli(self, ws, session, message):
                if message.get("request_id") in self._requests:
                    retry_joined.set()
                await super()._handle_cli(ws, session, message)

        bridge = ObservedBridge(TOKEN, request_timeout=0.02)
        async with host(bridge) as server, connect(url(server)) as extension:
            es = await authenticate(extension, "extension")
            rid = request_id(bridge)
            message = {"method": "state_change", "params": {"x": 1}, "request_id": rid}
            async with connect(url(server)) as first:
                cs = await authenticate(first)
                await first.send(cs.pack(message))
                command = es.unpack(await extension.recv())
                assert cs.unpack(await first.recv())["status"] == "in_flight"
            # Only the first request needs a short timeout. Synchronize the retry by event,
            # rather than assuming Windows can schedule two sockets within 20 milliseconds.
            bridge.request_timeout = 5
            async with connect(url(server)) as retry:
                cs = await authenticate(retry)
                await retry.send(cs.pack(message))
                await asyncio.wait_for(retry_joined.wait(), 5)
                await extension.send(es.pack({"id": command["id"], "result": "done"}))
                assert cs.unpack(await retry.recv())["result"] == "done"
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(extension.recv(), 0.01)
            async with connect(url(server)) as cached:
                cs = await authenticate(cached)
                assert (await ask(cached, cs, message))["result"] == "done"
            async with connect(url(server)) as conflict:
                cs = await authenticate(conflict)
                assert (await ask(conflict, cs, {**message, "method": "different"}))[
                    "status"
                ] == "conflict"
            assert len(bridge._requests) == 1

    asyncio.run(scenario())


def test_disconnect_during_send_returns_unknown_without_reexecution():
    class Gone:
        calls = 0

        async def send(self, _):
            self.calls += 1
            raise ConnectionClosedError(None, None)

    async def scenario():
        bridge = BridgeServer(TOKEN)
        gone = Gone()
        bridge._extension_ws, bridge._extension_session = gone, Session("key", "server")
        message = {"method": "state_change", "request_id": request_id(bridge)}
        async with host(bridge) as server:
            for _ in range(2):
                async with connect(url(server)) as client:
                    cs = await authenticate(client)
                    result = await ask(client, cs, message)
                    assert result["status"] == "unknown"
            assert gone.calls == 1 and not bridge._pending

    asyncio.run(scenario())


def test_old_server_request_id_is_not_replayed_after_restart():
    async def scenario():
        bridge = BridgeServer(TOKEN)
        async with host(bridge) as server, connect(url(server)) as client:
            cs = await authenticate(client)
            result = await ask(
                client, cs, {"method": "state_change", "request_id": "0" * 64 + ":" + "1" * 32}
            )
            assert result["status"] == "stale"
            assert not bridge._requests

    asyncio.run(scenario())


def test_malformed_handshake_is_rejected():
    async def scenario():
        bridge = BridgeServer(TOKEN)
        async with host(bridge) as server, connect(url(server)) as client:
            await client.send("[]")
            with pytest.raises(ConnectionClosed):
                await client.recv()

    asyncio.run(scenario())
