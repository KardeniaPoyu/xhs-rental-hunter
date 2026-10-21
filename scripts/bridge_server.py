"""Loopback bridge with mutual authentication and bounded request deduplication."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sys
from http import HTTPStatus

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from xhs.bridge_auth import PROTOCOL, Session, proof, valid_nonce

logger = logging.getLogger("xhs-bridge")
EXTENSION_ORIGIN = re.compile(r"chrome-extension://[a-p]{32}")


class BridgeServer:
    def __init__(
        self,
        token=None,
        *,
        handshake_timeout=3.0,
        max_unauthenticated=16,
        request_timeout=90.0,
        max_requests=128,
    ):
        self.token = token or os.environ.get("XHS_BRIDGE_TOKEN", "")
        if len(self.token) < 32:
            raise ValueError("请设置至少 32 字符的 XHS_BRIDGE_TOKEN，并在扩展中填写相同配对码。")
        self.server_id = secrets.token_hex(32)
        self.handshake_timeout = handshake_timeout
        self.max_unauthenticated = max_unauthenticated
        self.request_timeout = request_timeout
        self.max_requests = max_requests
        self._authenticating = 0
        self._extension_ws = None
        self._extension_session = None
        self._pending = {}
        self._requests = {}

    def process_request(self, connection, request):
        origins = request.headers.get_all("Origin")
        if len(origins) > 1 or (origins and not EXTENSION_ORIGIN.fullmatch(origins[0])):
            return connection.respond(HTTPStatus.FORBIDDEN, "Origin rejected\n")
        if self._authenticating >= self.max_unauthenticated:
            return connection.respond(HTTPStatus.SERVICE_UNAVAILABLE, "Bridge busy\n")
        return None

    async def handle(self, ws):
        origin = ws.request.headers.get("Origin") if ws.request else None
        if origin and not EXTENSION_ORIGIN.fullmatch(origin):
            await ws.close(code=1008, reason="Origin rejected")
            return
        if self._authenticating >= self.max_unauthenticated:
            await ws.close(code=1013, reason="Bridge busy")
            return
        self._authenticating += 1
        try:
            async with asyncio.timeout(self.handshake_timeout):
                raw = await ws.recv()
                if len(raw) > 4096:
                    raise ValueError("Handshake too large")
                hello = json.loads(raw)
                role, nonce = hello.get("role"), hello.get("client_nonce")
                if (
                    hello.get("protocol") != PROTOCOL
                    or role not in {"cli", "extension"}
                    or not valid_nonce(nonce)
                ):
                    raise ValueError("Invalid handshake")
                server_nonce = secrets.token_hex(32)
                parts = (role, nonce, server_nonce, self.server_id)
                await ws.send(
                    json.dumps(
                        {
                            "server_nonce": server_nonce,
                            "server_id": self.server_id,
                            "proof": proof(self.token, "server", *parts),
                        }
                    )
                )
                response = json.loads(await ws.recv())
                signature = response.get("proof")
                if not isinstance(signature, str) or not hmac.compare_digest(
                    signature, proof(self.token, "client", *parts)
                ):
                    raise ValueError("Pairing required")
                session = Session(proof(self.token, "session", *parts), "server")
        except (TimeoutError, ValueError, TypeError, AttributeError, ConnectionClosed):
            await ws.close(code=1008, reason="Authentication failed")
            return
        finally:
            self._authenticating -= 1

        try:
            if role == "extension":
                # No await between checking and owning the slot.
                if self._extension_ws is not None:
                    await ws.close(code=1008, reason="Extension already connected")
                    return
                self._extension_ws, self._extension_session = ws, session
                try:
                    await ws.send(session.pack({"ready": True, "server_id": self.server_id}))
                    await self._handle_extension(ws, session)
                finally:
                    if self._extension_ws is ws:
                        self._extension_ws, self._extension_session = None, None
                        for future in self._pending.values():
                            if not future.done():
                                future.set_result(
                                    {
                                        "error": "Extension 已断开；执行结果未知，请先核实。",
                                        "status": "unknown",
                                    }
                                )
                        self._pending.clear()
            else:
                await ws.send(session.pack({"ready": True, "server_id": self.server_id}))
                message = session.unpack(await asyncio.wait_for(ws.recv(), self.handshake_timeout))
                await self._handle_cli(ws, session, message)
        except (TimeoutError, ValueError, TypeError, AttributeError, ConnectionClosed):
            await ws.close(code=1008, reason="Invalid or closed session")

    async def _handle_extension(self, ws, session):
        async for raw in ws:
            message = session.unpack(raw)
            request_id = message.get("id")
            if isinstance(request_id, str) and request_id in self._pending:
                future = self._pending.pop(request_id)
                if not future.done():
                    future.set_result(message)

    async def _handle_cli(self, ws, session, message):
        async def reply(result):
            await ws.send(session.pack(result))

        if message.get("method") == "ping_server":
            await reply({"result": {"extension_connected": self._extension_ws is not None}})
            return
        request_id = message.get("request_id")
        if (
            not isinstance(request_id, str)
            or not request_id.startswith(self.server_id + ":")
            or not re.fullmatch(r"[0-9a-f]{64}:[0-9a-f]{32}", request_id)
        ):
            await reply(
                {"error": "请求编号不属于当前服务；旧请求不可自动重放。", "status": "stale"}
            )
            return
        command = {"method": message.get("method"), "params": message.get("params", {})}
        fingerprint = hashlib.sha256(json.dumps(command, sort_keys=True).encode()).hexdigest()
        entry = self._requests.get(request_id)
        if entry:
            if entry["fingerprint"] != fingerprint:
                await reply({"error": "相同请求编号不能用于不同命令。", "status": "conflict"})
                return
            future = entry["future"]
        else:
            if not self._extension_ws:
                await reply({"error": "Extension 未连接", "status": "not_started"})
                return
            if len(self._requests) >= self.max_requests:
                await reply(
                    {
                        "error": "本次服务的请求记录已满；核实未完成请求后重启服务。",
                        "status": "not_started",
                    }
                )
                return
            future = asyncio.get_running_loop().create_future()
            self._requests[request_id] = {"fingerprint": fingerprint, "future": future}
            self._pending[request_id] = future
            extension, extension_session = self._extension_ws, self._extension_session
            try:
                await extension.send(extension_session.pack({**command, "id": request_id}))
            except (ConnectionClosed, ConnectionError):
                if not future.done():
                    future.set_result(
                        {
                            "error": "Extension 发送中断；执行结果未知，请先核实。",
                            "status": "unknown",
                        }
                    )
                self._pending.pop(request_id, None)
        try:
            # Timeout never cancels the browser operation. The same ID only waits/retrieves it.
            result = await asyncio.wait_for(asyncio.shield(future), self.request_timeout)
            await reply({**result, "request_id": request_id})
        except TimeoutError:
            await reply(
                {
                    "error": "请求仍在执行；用同一请求编号查询，勿重新发起。",
                    "status": "in_flight",
                    "request_id": request_id,
                }
            )


async def main(port):
    bridge = BridgeServer()
    async with serve(
        bridge.handle,
        "127.0.0.1",
        port,
        max_size=1024 * 1024,
        max_queue=8,
        open_timeout=3,
        process_request=bridge.process_request,
    ):
        logger.info("Bridge server 已启动: ws://127.0.0.1:%d", port)
        await asyncio.Future()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="XHS Extension Bridge Server")
    parser.add_argument("--port", type=int, default=9333)
    asyncio.run(main(parser.parse_args().port))
