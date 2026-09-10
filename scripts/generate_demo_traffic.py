#!/usr/bin/env python3
"""Generate bounded, real local MCP traffic for dashboard demonstrations."""
import argparse
import json
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import check_governance as g
from trace_context import demo_trace, grafana_explore_url


def session(user):
    token = g.get_token(user,f'{user}-demo-password')
    _,result,sid = g.mcp_post({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'telemetry-demo','version':'1'}}},token)
    g.result_or_fail(result)
    if not sid: raise RuntimeError('Missing session')
    g.mcp_post({'jsonrpc':'2.0','method':'notifications/initialized'},token,sid)
    g.result_or_fail(g.mcp_post({'jsonrpc':'2.0','id':2,'method':'tools/list'},token,sid)[1])
    return token,sid


def unauthenticated(invalid=False):
    headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream'}
    if invalid:headers['Authorization']='Bearer deliberately-invalid-demo-token'
    req=Request(g.GATEWAY_URL,data=json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'telemetry-demo','version':'1'}}}).encode(),headers=headers)
    try:
        with urlopen(req,timeout=5):raise RuntimeError('Unauthenticated request unexpectedly succeeded')
    except HTTPError as e:
        if e.code!=401:raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cycles',type=int,default=12)
    parser.add_argument('--interval',type=float,default=3)
    args=parser.parse_args()
    if not 1<=args.cycles<=100 or not 0<=args.interval<=30:parser.error('cycles must be 1..100 and interval 0..30 seconds')
    g.wait_for_keycloak()
    alice=session('alice');bob=session('bob')
    counts={'hello_world':0,'add_numbers':0,'utc_time':0,'hidden_math':0,'authentication_401':0}
    for cycle in range(args.cycles):
        with demo_trace(f'demo workload {cycle+1}') as (trace_id,_):
            jobs=[('hello_world','hello_hello_world',{'message':'observability demo'},alice)]*6+[('add_numbers','math_add_numbers',{'a':cycle,'b':3},bob)]*3+[('utc_time','time_utc_time',{},alice)]
            for i,(label,name,arguments,(token,sid)) in enumerate(jobs):
                result=g.result_or_fail(g.mcp_post({'jsonrpc':'2.0','id':10+i,'method':'tools/call','params':{'name':name,'arguments':arguments}},token,sid)[1])
                if result.get('isError'):raise RuntimeError(f'{name} returned an application error')
                counts[label]+=1
            try:
                g.mcp_post({'jsonrpc':'2.0','id':30,'method':'tools/call','params':{'name':'math_add_numbers','arguments':{'a':2,'b':3}}},*alice)
            except RuntimeError as e:
                if '400' not in str(e) or 'Unknown tool' not in str(e):raise
                counts['hidden_math']+=1
            else:raise RuntimeError('Alice accessed hidden Math tool')
        unauthenticated();unauthenticated(True);counts['authentication_401']+=2
        print(f'Cycle {cycle+1}/{args.cycles}: {grafana_explore_url(trace_id)}',flush=True)
        if cycle+1<args.cycles:time.sleep(args.interval)
    print(json.dumps({'real_demo_requests':counts},indent=2))

if __name__=='__main__':main()
