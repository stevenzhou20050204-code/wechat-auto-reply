'use strict';
const path = require('node:path');
const mode = process.argv[2] || 'direct';
(async () => {
  const infoResponse = await fetch('https://mcp.desktopcommander.app/api/mcp-info', { signal: AbortSignal.timeout(10000) });
  if (!infoResponse.ok) throw new Error(`Public service HTTP ${infoResponse.status}`);
  const config = await infoResponse.json();
  if (mode === 'socks') require('./dc-network.cjs');
  if (mode === 'http') {
    const { ProxyAgent, setGlobalDispatcher } = require(path.join(__dirname, 'network-runtime/node_modules/undici'));
    setGlobalDispatcher(new ProxyAgent('http://127.0.0.1:21000'));
  }
  const count = Math.min(5, Math.max(1, Number(process.argv[3]) || 1));
  for (let attempt = 1; attempt <= count; attempt++) {
    const started = Date.now();
    try {
      const response = await fetch(config.supabaseUrl + '/auth/v1/health', { headers: { apikey: config.supabasePublishableKey }, signal: AbortSignal.timeout(10000) });
      await response.body.cancel();
      console.log(JSON.stringify({ mode, attempt, status: response.status, ms: Date.now() - started }));
    } catch (error) {
      console.log(JSON.stringify({ mode, attempt, error: error.cause?.code || error.name, ms: Date.now() - started }));
      process.exitCode = 1;
    }
  }
})().catch(error => { console.log(JSON.stringify({ mode, error: error.cause?.code || error.name, message: error.cause?.message || error.message })); process.exitCode = 1; });
