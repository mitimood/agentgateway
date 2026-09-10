"""Build the provisioned Grafana dashboard; queries count gateway SERVER spans once."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from trace_context import grafana_explore_url
TRACE_URL = grafana_explore_url()
AUTH_URL = grafana_explore_url('{ resource.service.name = "agentgateway" && span.http.status = 401 }')
HIDDEN_URL = grafana_explore_url('{ resource.service.name = "agentgateway" && span.access.outcome = "hidden_or_unknown_tool" }')
GUARDRAIL_URL = grafana_explore_url('{ resource.service.name = "agentgateway" && (span.access.outcome = "guardrail_blocked" || span.access.outcome = "guardrail_error") }')
BASE='service_name="agentgateway",span_kind="SPAN_KIND_SERVER",http_path="/mcp"'
CALL=BASE+',mcp_method_name="tools/call"'
MET='traces_span_metrics_calls_total'
DUR='traces_span_metrics_duration_milliseconds_bucket'
panels=[]
def panel(title,kind,x,y,w,h,queries,unit='short',description='',options=None):
 p={'id':len(panels)+1,'title':title,'type':kind,'gridPos':{'x':x,'y':y,'w':w,'h':h},'datasource':{'type':'prometheus','uid':'prometheus'},'description':description,'targets':[{'refId':chr(65+i),'expr':q,'legendFormat':label,'instant':kind in ['stat','bargauge','piechart'],'range':kind=='timeseries','exemplar':kind=='timeseries'} for i,(q,label) in enumerate(queries)],'fieldConfig':{'defaults':{'unit':unit,'min':0,'color':{'mode':'palette-classic'},'custom':{'lineWidth':2,'fillOpacity':15,'showPoints':'never'}},'overrides':[]},'options':options or {}}
 if kind=='stat':p['options']={'colorMode':'value','graphMode':'none','justifyMode':'auto','reduceOptions':{'calcs':['lastNotNull'],'fields':'','values':False}};p['fieldConfig']['defaults']['decimals']=0
 if kind=='timeseries':p['options']={'tooltip':{'mode':'multi','sort':'desc'},'legend':{'displayMode':'list','placement':'bottom'}}
 if title.startswith('Authentication failures'):p['fieldConfig']['defaults']['color']={'mode':'fixed','fixedColor':'red'}
 if title.startswith('Hidden / unknown'):p['fieldConfig']['defaults']['color']={'mode':'fixed','fixedColor':'orange'}
 if title.startswith('Tool calls'):p['fieldConfig']['defaults']['color']={'mode':'fixed','fixedColor':'blue'}
 for raw,label,color in [('ok','Successful','green'),('unauthenticated','Authentication failed','red'),('hidden_or_unknown_tool','Hidden / unknown tool','orange'),('forbidden','Forbidden','red'),('guardrail_blocked','Guardrail blocked','orange'),('guardrail_error','Guardrail unavailable / error','red'),('other_error','Other error','purple')]:
  p['fieldConfig']['overrides'].append({'matcher':{'id':'byName','options':raw},'properties':[{'id':'displayName','value':label},{'id':'color','value':{'mode':'fixed','fixedColor':color}}]})
 panels.append(p)
zero=lambda q:f'({q}) or vector(0)'
count=lambda filt:f'sum({MET}{{{filt}}})'
total=count(CALL)
failed=count(BASE+',access_outcome="unauthenticated"')
panel('Tool calls observed','stat',0,0,6,4,[(zero(total),'Calls')],description='Cumulative gateway tool-call attempts since the collector started; each request counts once.')
panel('Successful tool calls','stat',6,0,6,4,[(zero(count(CALL+',access_outcome="ok"')),'Successes')],description='HTTP-successful tool calls excluding ExtMCP guardrail rejections. Other application-level errors inside HTTP 200 are not distinguished.')
panel('Authentication failures · 401','stat',12,0,6,4,[(zero(failed),'Rejected')],description='Missing or invalid bearer credentials rejected by the gateway, since collector start.')
panel('Hidden / unknown tool attempts','stat',18,0,6,4,[(zero(count(CALL+',access_outcome="hidden_or_unknown_tool"')),'Rejected')],description='HTTP 400 with Unknown tool. In the Alice demo this is the hidden Math tool; an arbitrary unknown name is not proof of a policy denial.')
panel('Most used tools · successful calls','bargauge',0,4,12,8,[(f'sort_desc(sum by (demo_tool) ({MET}{{{CALL},access_outcome="ok"}}))','{{demo_tool}}')],description='Cumulative successful calls since collector start. Each demo target has exactly one tool; Collector maps target to its known tool name.',options={'orientation':'horizontal','displayMode':'gradient','showUnfilled':True,'reduceOptions':{'calcs':['lastNotNull'],'values':False}})
panel('Access outcomes · all MCP requests','piechart',12,4,12,8,[(f'sum by (access_outcome) ({MET}{{{BASE},access_outcome!=""}})','{{access_outcome}}')],description='Cumulative outcomes for all /mcp requests since collector start, including initialize/discovery. HTTP 401 = authentication, 403 = authorization, unknown/hidden is separate.',options={'pieType':'donut','displayLabels':['percent'],'legend':{'displayMode':'table','placement':'right','values':['value','percent']},'reduceOptions':{'calcs':['lastNotNull'],'values':False}})
panel('Tool traffic · calls per minute','timeseries',0,12,12,8,[(f'sum by (demo_tool) (rate({MET}{{{CALL}}}[$__rate_interval])) * 60','{{demo_tool}}')],unit='cpm',description='Rolling rate of gateway tool-call attempts. Selected time range applies. Exemplars link to individual traces.')
panel('Access failures over time','timeseries',12,12,12,8,[(f'sum by (access_outcome) (rate({MET}{{{BASE},access_outcome=~"unauthenticated|forbidden|hidden_or_unknown_tool|guardrail_blocked|guardrail_error"}}[$__rate_interval])) * 60','{{access_outcome}}')],unit='cpm')
panel('Tool latency · p95','timeseries',0,20,12,8,[(f'histogram_quantile(0.95, sum by (le,demo_tool) (rate({DUR}{{{CALL},access_outcome="ok"}}[$__rate_interval])))','{{demo_tool}}')],unit='ms',description='Estimated p95 from histogram buckets for successful gateway tool calls. Includes gateway and upstream round trip, not client/network time before gateway.')
panel('Tool success rate','timeseries',12,20,12,8,[(f'100 * sum by (demo_tool) (rate({MET}{{{CALL},access_outcome="ok"}}[$__rate_interval])) / sum by (demo_tool) (rate({MET}{{{CALL}}}[$__rate_interval]))','{{demo_tool}}')],unit='percent',description='HTTP success excluding guardrail rejections / all tool-call attempts. No traffic means no percentage, not 100%.')
panel('Guardrail rejections','stat',0,28,6,5,[(zero(count(CALL+',access_outcome=~"guardrail_blocked|guardrail_error"')),'Blocked')],description='Content policy denials and fail-closed policy errors since Collector start. These JSON-RPC errors can use HTTP 200; they are excluded from successful calls. Successful output redactions remain successful calls; inspect policy logs for redactions.')
panels.append({'id':len(panels)+1,'type':'text','title':'Reading the telemetry','gridPos':{'x':6,'y':28,'w':18,'h':5},'options':{'mode':'markdown','content':f'**Counters and rankings:** observations since Collector start. **Time-series charts:** selected time range.\n\nCounts use gateway **SERVER** spans only; policy and upstream child spans do not count again. ExtMCP rejections are identified from gateway error spans even with HTTP 200. Redacted responses remain successful. Other HTTP 200 application errors are not distinguished. Full sampling is enabled; latency percentiles are histogram estimates.\n\n[Explore requests]({TRACE_URL}) · [Guardrail rejections]({GUARDRAIL_URL})'}})
d={'uid':'mcp-gateway','title':'MCP Gateway · Access & Usage','description':'Gateway request telemetry: access failures, guardrails, popular tools and latency. Live local demo observations.','schemaVersion':41,'version':2,'editable':False,'refresh':'5s','time':{'from':'now-15m','to':'now'},'timezone':'browser','tags':['MCP','OpenTelemetry'],'panels':panels,'links':[{'title':'Explore traces','url':TRACE_URL},{'title':'Authentication failures','url':AUTH_URL},{'title':'Hidden tool attempts','url':HIDDEN_URL},{'title':'Guardrail rejections','url':GUARDRAIL_URL}]}
Path(__file__).with_name('dashboards').joinpath('gateway.json').write_text(json.dumps(d,indent=2)+'\n')
