#!/usr/bin/env python3
"""Exercise the installed/bundled stdio bridge using its interactive OAuth session."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def check(root: Path, node: str, manual_config: Path | None = None) -> None:
    env = dict(os.environ)
    env['AGENTGATEWAY_URL'] = 'http://localhost:3000/mcp'
    env['AGENTGATEWAY_CLIENT_ID'] = 'agentgateway'
    params = StdioServerParameters(command=node, args=[str(root / 'server/index.mjs')], env=env)
    if manual_config:
        server = json.loads(manual_config.read_text())['mcpServers']['agentgateway']
        env.update(server.get('env', {}))
        params = StdioServerParameters(command=server['command'], args=server.get('args', []), env=env)
    print('Checking ' + ('manual Claude configuration' if manual_config else 'desktop extension'), flush=True)
    async with stdio_client(params, errlog=sys.stderr) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=90) as session:
            await session.initialize()
            listing = await session.list_tools()
            names = [tool.name for tool in listing.tools]
            print(json.dumps({'visible_tools': names}), flush=True)
            hello = next(name for name in names if name.endswith('hello_world'))
            clock = next(name for name in names if name.endswith('utc_time'))
            calls = [(hello, {'message': 'desktop extension verified'}), (clock, {})]
            math = next((name for name in names if name.endswith('add_numbers')), None)
            if math:
                calls.append((math, {'a': 2, 'b': 3}))
            for name, arguments in calls:
                result = await session.call_tool(name, arguments)
                assert not result.is_error, result
                texts = [item.text for item in result.content if hasattr(item, 'text')]
                print(json.dumps({'tool': name, 'result': texts}), flush=True)
                if name == hello:
                    assert 'desktop extension verified' in ' '.join(texts)
                if name == math:
                    assert texts == ['math result from the math MCP server: 5']
            redacted = await session.call_tool(hello, {'message': 'Contact demo@example.com'})
            text = ' '.join(item.text for item in redacted.content if hasattr(item, 'text'))
            assert '[REDACTED_EMAIL]' in text and 'demo@example.com' not in text
            print('OK: OAuth, stdio bridge, tool discovery/calls and gateway redaction', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--extension', type=Path, default=Path(__file__).resolve().parents[1] / 'desktop-extension')
    parser.add_argument('--node', default=shutil.which('node'))
    parser.add_argument('--manual-config', type=Path, help='Instead test the agentgateway entry in a Claude desktop config file.')
    args = parser.parse_args()
    if not args.node:
        parser.error('Node.js was not found; pass --node explicitly.')
    asyncio.run(check(args.extension.resolve(), args.node, args.manual_config))
