"""Check the real gRPC handler without producing policy audit noise."""

import grpc

from guardrails.proto import ext_mcp_pb2 as pb
from guardrails.proto import ext_mcp_pb2_grpc as rpc


if __name__ == "__main__":
    with grpc.insecure_channel("127.0.0.1:9001") as channel:
        result = rpc.ExtMcpStub(channel).CheckRequest(pb.McpRequest(method="ping"), timeout=2)
    if result.WhichOneof("result") != "pass":
        raise SystemExit("Guardrail health check failed")
