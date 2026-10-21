/* Protocol v2: shared-secret proofs and direction/sequence-bound messages. */
globalThis.BridgeAuth = (() => {
  const utf8 = new TextEncoder();
  const hex = bytes => Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
  const nonce = () => hex(crypto.getRandomValues(new Uint8Array(32)));
  async function proof(secret, ...parts) {
    const key = await crypto.subtle.importKey("raw", utf8.encode(secret),
      { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    return hex(new Uint8Array(await crypto.subtle.sign("HMAC", key, utf8.encode(parts.join("|")))));
  }
  function equal(a, b) {
    if (typeof a !== "string" || a.length !== b.length) return false;
    let different = 0;
    for (let i = 0; i < a.length; i++) different |= a.charCodeAt(i) ^ b.charCodeAt(i);
    return different === 0;
  }
  class Session {
    constructor(key) { this.key = key; this.sent = 0; this.received = 0; }
    async pack(message) {
      const body = JSON.stringify({ seq: ++this.sent, message });
      return JSON.stringify({ body, proof: await proof(this.key, "client", body) });
    }
    async unpack(raw) {
      const frame = JSON.parse(raw);
      if (typeof frame.body !== "string" ||
          !equal(frame.proof, await proof(this.key, "server", frame.body))) {
        throw new Error("Bridge message authentication failed");
      }
      const value = JSON.parse(frame.body);
      if (value.seq !== this.received + 1 || !value.message ||
          typeof value.message !== "object" || Array.isArray(value.message)) {
        throw new Error("Bridge replay or malformed frame");
      }
      this.received++;
      return value.message;
    }
  }
  const hello = () => ({ protocol: 2, role: "extension", client_nonce: nonce() });
  async function challenge(secret, greeting, response) {
    if (!/^[0-9a-f]{64}$/.test(response.server_nonce || "") ||
        !/^[0-9a-f]{64}$/.test(response.server_id || "")) throw new Error("Invalid bridge challenge");
    const parts = [greeting.role, greeting.client_nonce, response.server_nonce, response.server_id];
    if (!equal(response.proof, await proof(secret, "server", ...parts))) {
      throw new Error("Cannot authenticate bridge server");
    }
    return {
      response: { proof: await proof(secret, "client", ...parts) },
      session: new Session(await proof(secret, "session", ...parts))
    };
  }
  return { hello, challenge, proof, Session };
})();
