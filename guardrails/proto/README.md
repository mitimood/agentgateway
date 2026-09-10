# ExtMCP protocol provenance

`ext_mcp.proto` is copied unchanged from agentgateway **v1.5.0**, commit
`fe6732474a96a0363dfb9822859af4e9bab360fa`:
https://github.com/agentgateway/agentgateway/blob/v1.5.0/crates/protos/proto/ext_mcp.proto

The upstream Apache-2.0 license is included in `LICENSE`. The two `*_pb2*.py`
files are generated with the pinned `grpcio-tools` development dependency:

```sh
uv run --frozen python -m grpc_tools.protoc -I . \
  --python_out=. --grpc_python_out=. guardrails/proto/ext_mcp.proto
```

The generated code is checked in so running the demo requires no compiler or
upstream source downloads. Request parameters are never mutated by this demo;
the gateway's original tool identity remains the authorization input.
