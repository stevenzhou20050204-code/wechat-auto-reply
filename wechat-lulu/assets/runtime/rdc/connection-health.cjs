'use strict';
const fs = require('node:fs');
const path = require('node:path');
// 启动阶段也要有本进程的报告，不能一直沿用上一次开机的 PID。
const bootChannel = {};
let latestChannel = bootChannel;
let startupStage = 'bootstrap';
const startupAt = performance.now();
function snapshot(channel, now = performance.now()) {
  if (channel.channel?.state === 'joined') channel._dcNotJoinedSince = null;
  else if (channel._dcNotJoinedSince == null) channel._dcNotJoinedSince = now;
  return {
    pid: process.pid, updatedAt: new Date().toISOString(),
    channelState: channel.channel?.state ?? 'uninitialized',
    lastPongAgeMs: channel.lastHeartbeatOkAt == null ? null : Math.max(0, Math.round(now - channel.lastHeartbeatOkAt)),
    activeCalls: channel._dcActiveCalls ?? 0,
    reconnectAttempt: channel.reconnectAttempt ?? 0,
    reconnecting: !!channel.isRecreatingChannel,
    notJoinedForMs: channel._dcNotJoinedSince == null ? 0 : Math.max(0, Math.round(now - channel._dcNotJoinedSince)),
    authenticationLost: !!channel.sessionLost || startupStage === 'authentication-required',
    authRecoveryVersion: 1,
    authenticationStatus: startupStage === 'authentication-required' ? 'owner-required' :
      (startupStage === 'session-restore-unavailable' || channel.sessionLost) ? 'restore-unavailable' : 'unverified',
    shuttingDown: !!channel.shuttingDown,
    startupStage, startupAgeMs: Math.round(now - startupAt)
  };
}
function report(channel = latestChannel) {
    if (!process.env.DC_HEALTH_PATH || !process.argv.includes('remote')) return;
    latestChannel = channel;
  try {
    const file = path.resolve(process.env.DC_HEALTH_PATH);
    const temp = file + '.' + process.pid + '.tmp';
    fs.writeFileSync(temp, JSON.stringify(snapshot(channel)) + '\n', { mode: 0o600 });
    fs.renameSync(temp, file);
  } catch (error) {
    console.error('[DC health] Cannot publish connection health:', error.code || error.name);
  }
}
globalThis.__dcReportHealth = report;
globalThis.__dcStartupStage = stage => {
  startupStage = stage;
  report();
};
if (process.env.DC_HEALTH_PATH && process.argv.includes('remote')) {
  report();
  // 远端请求只有初始化订阅后才能执行；之后使用真实通道的执行计数。
  setInterval(() => report(), 10000).unref();
  process.stderr.write(`[DC startup] health publisher ready for PID ${process.pid}\n`);
}
module.exports = { snapshot, report };
