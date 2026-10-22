'use strict';
require('./connection-health.cjs');
// Process-local routing only. TLS certificate verification remains enabled.
const path = require('node:path');
const tls = require('node:tls');
const diagnostics = require('node:diagnostics_channel');
const { Dispatcher, Agent, setGlobalDispatcher } = require(path.join(__dirname, 'network-runtime/node_modules/undici'));
const { SocksClient } = require(path.join(__dirname, 'network-runtime/node_modules/socks'));
// The public service is reachable directly on this network (verified locally).
// Only Supabase needs the SOCKS route; do not slow every service request with it.
const backendHosts = new Set(['olvbkozcufcbptfogatw.supabase.co']);
const direct = new Agent();
const transientCodes = new Set(['ECONNRESET', 'ECONNREFUSED', 'ETIMEDOUT', 'EPIPE', 'ERR_SSL_WRONG_VERSION_NUMBER', 'ERR_SSL_UNEXPECTED_EOF_WHILE_READING', 'ERR_SSL_INAPPROPRIATE_FALLBACK']);
async function establishTunnel(options) {
  for (let attempt = 1; attempt <= 4; attempt++) {
    let socket;
    try {
      const info = await SocksClient.createConnection({
        proxy: { host: '127.0.0.1', port: 21001, type: 5 },
        command: 'connect',
        destination: { host: options.hostname, port: Number(options.port) || 443 },
        timeout: 10000,
        set_tcp_nodelay: true
      });
      socket = info.socket;
      socket.setKeepAlive(true, 15000);
      if (options.protocol === 'https:') {
        const secure = tls.connect({ socket, servername: options.servername || options.hostname, rejectUnauthorized: true, ALPNProtocols: ['http/1.1'] });
        socket = secure;
        secure.setKeepAlive(true, 15000);
        await new Promise((resolve, reject) => {
          const timer = setTimeout(() => secure.destroy(Object.assign(new Error('TLS handshake timeout'), { code: 'ETIMEDOUT' })), 10000);
          function ready() { clearTimeout(timer); secure.removeListener('error', fail); resolve(); }
          function fail(error) { clearTimeout(timer); secure.removeListener('secureConnect', ready); reject(error); }
          secure.once('secureConnect', ready);
          secure.once('error', fail);
        });
      }
      return socket;
    } catch (error) {
      socket?.destroy();
      const code = error.code || (String(error.message).includes('timed out') ? 'ETIMEDOUT' : 'SOCKS_CONNECT_FAILED');
      if (attempt === 4 || !transientCodes.has(code)) throw error;
      // The HTTP dispatcher has not received a socket yet: no HTTP request,
      // token refresh, tool result or other operation has been sent or replayed.
      console.error(`[DC network] ${new Date().toISOString()} pre-request handshake retry ${attempt}/3 ${options.hostname} ${code}`);
      await new Promise(resolve => setTimeout(resolve, 350 * 2 ** (attempt - 1)));
    }
  }
}
const proxy = new Agent({
  // 保留可用隧道并限制并发建连，避免每次领取指令都重新进行不稳定的握手。
  connections: 2,
  pipelining: 1,
  keepAliveTimeout: 60000,
  keepAliveMaxTimeout: 120000,
  connect(options, callback) {
    establishTunnel(options).then(socket => callback(null, socket), error => callback(error, null));
  }
});
class DesktopCommanderDispatcher extends Dispatcher {
  dispatch(options, handler) {
    const hostname = new URL(options.origin).hostname;
    return (backendHosts.has(hostname) ? proxy : direct).dispatch(options, handler);
  }
}
setGlobalDispatcher(new DesktopCommanderDispatcher());
diagnostics.channel('undici:request:error').subscribe(({ request, error }) => {
  try {
    const url = new URL(request.path, request.origin);
    if (!backendHosts.has(url.hostname)) return;
    // Deliberately omit headers, query strings, request bodies and tokens.
    console.error(`[DC network] ${new Date().toISOString()} ${request.method} ${url.origin}${url.pathname} ${error.code || error.name || 'network error'}`);
  } catch {}
});
console.error('[DC network] Public service direct; Supabase HTTPS/WebSocket use local SOCKS5 127.0.0.1:21001');
