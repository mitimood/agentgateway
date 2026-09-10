#!/usr/bin/env python3
"""Check live MCP credentials and network isolation in the local Compose stack."""

import json
from pathlib import Path
import subprocess

from check_governance import get_token


ROOT = Path(__file__).resolve().parents[1]
SERVERS = ("hello", "math", "time")

# Mint only test tokens inside a temporary, networkless initializer container.
# The signing key never leaves its Docker volume, including during validation.
TOKEN_PROBE = r'''
import json,stat
from pathlib import Path
from fastmcp.server.auth.providers.jwt import RSAKeyPair
from pydantic import SecretStr
private=Path('/run/keys/private/gateway-private.pem')
public=Path('/run/keys/public/gateway-public.pem')
assert stat.S_IMODE(private.stat().st_mode)==0o400 and private.stat().st_uid==65532
assert stat.S_IMODE(private.parent.stat().st_mode)==0o700
key=RSAKeyPair(private_key=SecretStr(private.read_text()),public_key=public.read_text())
result={}
for name in ('hello','math','time'):
    options=dict(subject='agentgateway',issuer='urn:agentgateway:backend',
                 audience=f'urn:mcp:{name}',scopes=['mcp:invoke'],expires_in_seconds=60)
    result[name]={
        'valid':key.create_token(**options),
        'expired':key.create_token(**{**options,'expires_in_seconds':-1}),
        'other server':key.create_token(**{**options,'audience':'urn:mcp:'+('math' if name!='math' else 'hello')})}
print(json.dumps(result))
'''

# Tokens travel over stdin, never command arguments or diagnostic output.
HTTP_PROBE = r'''
import json,sys
from urllib.request import Request,urlopen
from urllib.error import HTTPError
data=json.load(sys.stdin)
init={"jsonrpc":"2.0","id":1,"method":"initialize","params":{
    "protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"backend-auth-check","version":"1"}}}
def post(payload,token=None,session=None):
    headers={"Content-Type":"application/json","Accept":"application/json, text/event-stream"}
    if token:headers["Authorization"]="Bearer "+token
    if session:headers["Mcp-Session-Id"]=session
    request=Request("http://127.0.0.1:8000/mcp",data=json.dumps(payload).encode(),headers=headers)
    try:
        with urlopen(request,timeout=5) as response:
            body=response.read()
            if "text/event-stream" in response.headers.get("Content-Type",""):
                body=next((line[5:].strip() for line in body.decode().splitlines() if line.startswith("data:")),"null")
            return response.status,json.loads(body) if body else None,response.headers.get("Mcp-Session-Id")
    except HTTPError as error:return error.code,None,None
for name,token in data["denied"].items():
    status,_,_=post(init,token)
    if status!=401:raise RuntimeError(f"{name}: expected 401, got {status}")
status,body,session=post(init,data["valid"])
assert status==200 and body and "result" in body and session,"authenticated initialization failed"
post({"jsonrpc":"2.0","method":"notifications/initialized"},data["valid"],session)
call={"jsonrpc":"2.0","id":2,"method":"tools/call","params":data["call"]}
assert post(call,None,session)[0]==401,"session bypassed authentication"
status,body,_=post(call,data["valid"],session)
assert status==200 and "result" in body and not body["result"].get("isError",False),"authenticated tool call failed"
print("missing/invalid/expired/cross-server/user tokens rejected; valid service call succeeds")
'''


def docker(*args, **kwargs):
    return subprocess.run(["docker", *args], cwd=ROOT, check=True,
                          text=True, capture_output=True, **kwargs).stdout


def inspect(service):
    container = docker("compose", "ps", "-q", service).strip()
    if not container:
        raise RuntimeError(f"{service} is not running")
    return json.loads(docker("inspect", container))[0]


def main():
    users = {name: get_token(name, f"{name}-demo-password") for name in ("alice", "bob")}
    gateway = inspect("agentgateway")
    private_mount = next(m for m in gateway["Mounts"] if m["Destination"] == "/run/secrets")
    assert private_mount["Type"] == "volume" and not private_mount["RW"], "gateway key must be a read-only volume"
    backends = {name: inspect(f"mcp-{name}") for name in SERVERS}
    policy = inspect("guardrails")
    policy_networks = policy["NetworkSettings"]["Networks"]
    assert len(policy_networks) == 1, "guardrails: unexpected network membership"
    network = json.loads(docker("network", "inspect", next(iter(policy_networks))))[0]
    assert network["Internal"] and set(network["Containers"]) == {gateway["Id"], policy["Id"]}, "guardrails: expected a gateway-only internal network"
    assert not policy["Mounts"], "guardrails must not receive keys or other mounts"
    assert not any(policy["HostConfig"]["PortBindings"].values()), "guardrails must not publish a host port"
    print("guardrails: isolated gateway-only network; no key mounts or published ports")
    credentials = json.loads(docker("compose", "run", "--rm", "-T", "--no-deps",
                                    "--entrypoint", "/app/.venv/bin/python", "backend-auth-init", "-c", TOKEN_PROBE))
    for name, backend in backends.items():
        networks = backend["NetworkSettings"]["Networks"]
        assert len(networks) == 1, f"{name}: extra network exposes the backend"
        network_name = next(iter(networks))
        network = json.loads(docker("network", "inspect", network_name))[0]
        assert network["Internal"], f"{name}: backend network is not internal"
        assert set(network["Containers"]) == {gateway["Id"], backend["Id"]}, f"{name}: unexpected network peer"
        assert not any(backend["HostConfig"]["PortBindings"].values()), f"{name}: port published to host"
        assert not any(m.get("Name") == private_mount["Name"] for m in backend["Mounts"]), f"{name}: signing key mounted"
        public_mount = next(m for m in backend["Mounts"] if m["Destination"] == "/run/secrets")
        assert public_mount["Type"] == "volume" and not public_mount["RW"], f"{name}: public key mount must be read-only"
        denied = {"missing": None, "malformed": "not-a-token", **users,
                  **{k: v for k, v in credentials[name].items() if k != "valid"}}
        calls = {"hello": {"name": "hello_world", "arguments": {}},
                 "math": {"name": "add_numbers", "arguments": {"a": 2, "b": 3}},
                 "time": {"name": "utc_time", "arguments": {}}}
        result = docker("compose", "exec", "-T", f"mcp-{name}", "/app/.venv/bin/python", "-c", HTTP_PROBE,
                        input=json.dumps({"denied": denied, "valid": credentials[name]["valid"], "call": calls[name]}))
        print(f"{name}: {result.strip()}; isolated gateway-only network")

    # Use the actual IP, so a DNS failure alone cannot pass this isolation check.
    math_ip = next(iter(backends["math"]["NetworkSettings"]["Networks"].values()))["IPAddress"]
    probe = """import socket,sys
try:
    socket.create_connection((sys.argv[1],8000),timeout=2).close()
except OSError:
    print('OK: Hello cannot open a direct connection to Math')
else:
    raise SystemExit('FAIL: cross-server network bypass')
"""
    print(docker("compose", "exec", "-T", "mcp-hello", "python", "-c", probe, math_ip).strip())


if __name__ == "__main__":
    main()
