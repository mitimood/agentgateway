#!/usr/bin/env python3
"""Validate PromQL and exact request counts, accounting for counter resets."""
from collections import defaultdict
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlencode
from urllib.request import urlopen

PROMETHEUS_URL=os.getenv('PROMETHEUS_URL','http://127.0.0.1:9091')
BASE='service_name="agentgateway",span_kind="SPAN_KIND_SERVER",http_path="/mcp"'

COUNTER='traces_span_metrics_calls_total{'+BASE+'}'

def query(expression, *, at=None):
    params={'query':expression}
    if at is not None:params['time']=at
    with urlopen(PROMETHEUS_URL+'/api/v1/query?'+urlencode(params),timeout=5) as response:
        result=json.load(response)
    if result.get('status')!='success':raise RuntimeError(result)
    return result['data']['result']

def series_key(row):
    # Compute resets per original series BEFORE grouping tools/outcomes.
    return tuple(sorted(row['metric'].items()))


def counter_deltas(before, after, started):
    """Sum stored sample increments, including resets, without extrapolation.

    Endpoint subtraction loses requests when a counter goes 2 -> 0 -> 2.
    PromQL increase() handles resets but extrapolates fractional counts; use
    the actual stored samples for this exact-count smoke test instead.
    """
    previous = {series_key(row): float(row['values'][-1][1]) for row in before}
    totals = defaultdict(float)
    for row in after:
        value_before = previous.get(series_key(row), 0.0)
        increase = 0.0
        for timestamp, raw_value in row['values']:
            if timestamp <= started:
                continue
            value = float(raw_value)
            increase += value - value_before if value >= value_before else value
            value_before = value
        labels = row['metric']
        totals[(labels.get('demo_tool', ''), labels.get('access_outcome', ''))] += increase
    return dict(totals)

def main():
    root=Path(__file__).resolve().parents[1]
    dashboard=json.loads((root/'observability/grafana/dashboards/gateway.json').read_text())
    checked=0
    for panel in dashboard['panels']:
        for target in panel.get('targets',[]):
            query(target['expr'].replace('$__rate_interval','5m'))
            checked+=1
    started=time.time()
    # Freeze the baseline and retain each series' real sample timestamps.
    before=query(COUNTER+'[5m]',at=started)
    subprocess.run([sys.executable,str(root/'scripts/generate_demo_traffic.py'),'--cycles','1','--interval','0'],check=True)
    subprocess.run([sys.executable,str(root/'scripts/check_guardrails.py')],check=True)
    expected={('hello_world','ok'):9,('add_numbers','ok'):4,('utc_time','ok'):2,
              ('hello_world','guardrail_blocked'):3,('add_numbers','guardrail_blocked'):1,
              ('add_numbers','hidden_or_unknown_tool'):1,('','unauthenticated'):2}
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        now=time.time()
        # Keep every sample since the baseline, even if a subprocess was slow.
        window=max(1,math.ceil(now-started)+1)
        after=query(f'{COUNTER}[{window}s]',at=now)
        observed=counter_deltas(before,after,started)
        delta={k:observed.get(k,0) for k in expected}
        if delta==expected:
            print(f'OK: {checked} dashboard queries parse; exact request counts match (counter resets handled; no child-span double counting).')
            return
        time.sleep(1)
    raise RuntimeError(f'Expected {expected}, observed {delta}. Counts include observed counter resets. '
                       'Check Collector delivery and concurrent traffic; run against an otherwise idle demo.')

if __name__=='__main__':main()
