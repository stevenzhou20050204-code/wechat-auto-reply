'use strict';
(async () => {
  const response = await fetch('https://mcp.desktopcommander.app/api/mcp-info', { signal: AbortSignal.timeout(30000) });
  if (!response.ok) throw new Error('Main service HTTP ' + response.status);
  const config = await response.json();
  if (new URL(config.supabaseUrl).hostname !== 'olvbkozcufcbptfogatw.supabase.co') throw new Error('Backend address changed; update network routing before restarting');
  const health = await fetch(config.supabaseUrl + '/auth/v1/health', { headers: { apikey: config.supabasePublishableKey }, signal: AbortSignal.timeout(30000) });
  if (!health.ok) throw new Error('Backend health HTTP ' + health.status);
  await health.body.cancel();
  process.exit(0);
})().catch(error => { console.error('[preflight]', error.cause?.code || error.message); process.exit(1); });
