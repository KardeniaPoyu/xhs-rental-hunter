"""Mutual HMAC authentication and replay-protected frames; secrets never cross the socket."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets

PROTOCOL = 2


def proof(secret: str, *parts: str) -> str:
    return hmac.new(secret.encode(), "|".join(parts).encode(), hashlib.sha256).hexdigest()


def valid_nonce(value) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))


class Session:
    def __init__(self, key: str, side: str):
        self.key = key
        self.side = side
        self.sent = 0
        self.received = 0

    def pack(self, message: dict) -> str:
        self.sent += 1
        body = json.dumps({"seq": self.sent, "message": message}, ensure_ascii=False)
        return json.dumps({"body": body, "proof": proof(self.key, self.side, body)})

    def unpack(self, raw: str) -> dict:
        envelope = json.loads(raw)
        body = envelope.get("body")
        signature = envelope.get("proof")
        peer = "server" if self.side == "client" else "client"
        if (
            not isinstance(body, str)
            or not isinstance(signature, str)
            or not hmac.compare_digest(signature, proof(self.key, peer, body))
        ):
            raise ValueError("Bridge message authentication failed")
        data = json.loads(body)
        if data.get("seq") != self.received + 1 or not isinstance(data.get("message"), dict):
            raise ValueError("Bridge replay or malformed frame")
        self.received += 1
        return data["message"]


def client_hello(role="cli"):
    return {"protocol": PROTOCOL, "role": role, "client_nonce": secrets.token_hex(32)}


def client_challenge(token, hello, challenge):
    server_nonce, server_id = challenge.get("server_nonce"), challenge.get("server_id")
    if not valid_nonce(server_nonce) or not valid_nonce(server_id):
        raise ValueError("Invalid bridge challenge")
    parts = (hello["role"], hello["client_nonce"], server_nonce, server_id)
    signature = challenge.get("proof")
    if not isinstance(signature, str) or not hmac.compare_digest(
        signature, proof(token, "server", *parts)
    ):
        raise ValueError("Cannot authenticate bridge server")
    session = Session(proof(token, "session", *parts), "client")
    return {"proof": proof(token, "client", *parts)}, session


def authenticate_sync(ws, token):
    hello = client_hello()
    ws.send(json.dumps(hello))
    response, session = client_challenge(token, hello, json.loads(ws.recv(timeout=3)))
    ws.send(json.dumps(response))
    ready = session.unpack(ws.recv(timeout=3))
    if ready.get("ready") is not True:
        raise ValueError("Bridge not ready")
    return session, ready["server_id"]
