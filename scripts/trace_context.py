"""Export a real enclosing demo span through OTLP/HTTP using the standard JSON wire format."""
from contextlib import contextmanager
import json
import os
import secrets
import time
from urllib.request import Request, urlopen

@contextmanager
def demo_trace(name):
    trace_id, span_id = secrets.token_hex(16), secrets.token_hex(8)
    previous = os.environ.get('TRACEPARENT')
    os.environ['TRACEPARENT'] = f'00-{trace_id}-{span_id}-01'
    start = time.time_ns()
    failed = False
    try:
        yield trace_id, span_id
    except Exception:
        failed = True
        raise
    finally:
        end = time.time_ns()
        if previous is None:
            os.environ.pop('TRACEPARENT', None)
        else:
            os.environ['TRACEPARENT'] = previous
        span = {'traceId':trace_id,'spanId':span_id,'name':name,'kind':1,
                'startTimeUnixNano':str(start),'endTimeUnixNano':str(end),
                'status':{'code':2 if failed else 1},
                'attributes':[{'key':'demo.traffic','value':{'boolValue':True}}]}
        payload = {'resourceSpans':[{'resource':{'attributes':[{'key':'service.name','value':{'stringValue':'mcp-demo-client'}}]},
                   'scopeSpans':[{'scope':{'name':'mcp-demo'},'spans':[span]}]}]}
        req = Request(os.getenv('OTEL_HTTP_URL','http://127.0.0.1:4318/v1/traces'),
                      data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        with urlopen(req,timeout=5) as response:
            result = json.load(response)
            if result.get('partialSuccess',{}).get('rejectedSpans',0):
                raise RuntimeError('Collector rejected the demo root span')


def grafana_explore_url(query='{ resource.service.name = "agentgateway" }'):
    """Link directly to the provisioned trace data source inside Grafana Explore."""
    from urllib.parse import urlencode
    panes = {'trace': {'datasource': 'tempo', 'queries': [{
        'refId': 'A', 'datasource': {'type': 'tempo', 'uid': 'tempo'},
        # Grafana's TraceQL editor also resolves a bare trace ID directly.
        'queryType': 'traceql',
        'query': query, 'limit': 20, 'tableType': 'traces', 'filters': [],
    }], 'range': {'from': 'now-1h', 'to': 'now'}}}
    return os.getenv('GRAFANA_URL', 'http://localhost:3001').rstrip('/') + '/explore?' + urlencode({
        'schemaVersion': '1', 'panes': json.dumps(panes, separators=(',', ':')), 'orgId': '1',
    })
