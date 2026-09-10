"""Serve agentgateway's native MCP request and response policy hooks over gRPC."""

from concurrent import futures
import json
import logging
import signal

import grpc

from guardrails.policy import Denied, TOOLS, check_request, redact_response
from guardrails.proto import ext_mcp_pb2 as pb
from guardrails.proto import ext_mcp_pb2_grpc as rpc


LOG = logging.getLogger("demo.guardrails")


def audit(phase, targets, decision, rule):
    # Fixed labels only: never record arguments, results, headers or credentials.
    target = targets[0] if len(targets) == 1 and targets[0] in TOOLS else "unknown"
    LOG.info(json.dumps({"event": "guardrail", "phase": phase, "target": target,
                         "decision": decision, "rule": rule}))


def rejection(error):
    return pb.AuthorizationError(code=pb.AuthorizationError.PERMISSION_DENIED, reason=str(error))


class DemoGuardrails(rpc.ExtMcpServicer):
    def CheckRequest(self, request, context):
        if request.method != "tools/call":
            return pb.McpRequestResult(**{"pass": pb.Pass()})
        try:
            check_request(list(request.service_names), request.mcp_request)
        except Denied as error:
            audit("request", request.service_names, "deny", error.rule)
            return pb.McpRequestResult(error=rejection(error))
        audit("request", request.service_names, "allow", "within_limits")
        return pb.McpRequestResult(**{"pass": pb.Pass()})

    def CheckResponse(self, request, context):
        if request.method != "tools/call":
            return pb.McpResponseResult(**{"pass": pb.Pass()})
        try:
            clean = redact_response(request.mcp_response)
        except Denied as error:
            audit("response", request.service_names, "deny", error.rule)
            return pb.McpResponseResult(error=rejection(error))
        if clean is not None:
            audit("response", request.service_names, "redact", "email_output")
            return pb.McpResponseResult(mutated=clean)
        audit("response", request.service_names, "allow", "clean_output")
        return pb.McpResponseResult(**{"pass": pb.Pass()})


def create_server():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4), options=[
        ("grpc.max_receive_message_length", 128 * 1024),
        ("grpc.max_send_message_length", 128 * 1024),
    ])
    rpc.add_ExtMcpServicer_to_server(DemoGuardrails(), server)
    return server


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    server = create_server()
    server.add_insecure_port("0.0.0.0:9001")
    server.start()
    signal.signal(signal.SIGTERM, lambda *_: server.stop(2))
    LOG.info("Demo guardrails ready on :9001")
    server.wait_for_termination()


if __name__ == "__main__":
    main()
