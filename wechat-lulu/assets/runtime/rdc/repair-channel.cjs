'use strict';
const fs = require('node:fs');
const path = require('node:path');
const root = __dirname;
const file = path.join(root, 'app/node_modules/@wonderwhy-er/desktop-commander/dist/remote-device/remote-channel.js');
let text = fs.readFileSync(file, 'utf8');
if (text.includes('// DC_LOCAL_RELIABILITY_V1')) { console.log('Reliability patch already installed.'); process.exit(0); }
const backup = path.join(root, 'backups', 'remote-channel.original.js');
fs.mkdirSync(path.dirname(backup), { recursive: true });
if (!fs.existsSync(backup)) fs.copyFileSync(file, backup);
function replace(before, after) {
  if (!text.includes(before)) throw new Error('Installed RDC source changed; refused to patch an unknown version.');
  text = text.replace(before, after);
}
replace('async function clockAwareFetch(input, init) {\n    const response = await fetch(input, init);',
`// DC_LOCAL_RELIABILITY_V1: bound network waits without replaying requests.
async function clockAwareFetch(input, init) {
    const deadline = AbortSignal.timeout(30000);
    const callerSignal = init?.signal ?? (input instanceof Request ? input.signal : undefined);
    const signal = callerSignal ? AbortSignal.any([callerSignal, deadline]) : deadline;
    const response = await fetch(input, { ...init, signal });`);
replace('    checkConnectionHealth() {', '    checkConnectionHealth() {\n        globalThis.__dcReportHealth?.(this);');
replace(`if (status === 'ok')
                        this.lastHeartbeatOkAt = performance.now();`,
`if (status === 'ok') {
                        this.lastHeartbeatOkAt = performance.now();
                        globalThis.__dcReportHealth?.(this);
                    }`);
replace(`    dispatchToolCall(payload) {
        try {
            const maybePromise = this.onToolCall?.(payload);
            if (maybePromise instanceof Promise) {
                maybePromise.catch((e) => {
                    console.error('[DEBUG] Tool call handler rejected:', e?.message);
                });
            }
        }
        catch (e) {
            console.error('[DEBUG] Tool call handler threw:', e?.message);
        }
    }`,
`    dispatchToolCall(payload) {
        this._dcActiveCalls = (this._dcActiveCalls ?? 0) + 1;
        globalThis.__dcReportHealth?.(this);
        const finished = () => {
            this._dcActiveCalls = Math.max(0, this._dcActiveCalls - 1);
            globalThis.__dcReportHealth?.(this);
        };
        try {
            const maybePromise = this.onToolCall?.(payload);
            Promise.resolve(maybePromise).catch((e) => {
                console.error('[DEBUG] Tool call handler rejected:', e?.message);
            }).finally(finished);
        } catch (e) {
            console.error('[DEBUG] Tool call handler threw:', e?.message);
            finished();
        }
    }`);
replace(`        this.heartbeatInterval = setTimeout(async () => {
            if (this.heartbeatDeviceId) {
                await this.updateHeartbeat(this.heartbeatDeviceId);
            }
            this.scheduleHeartbeat(); // re-read the tier every tick
        }, this.heartbeatIntervalMs());`,
`        this.heartbeatInterval = setTimeout(async () => {
            try {
                if (this.heartbeatDeviceId) {
                    await this.withTimeout(() => this.updateHeartbeat(this.heartbeatDeviceId), 40000, 'Bookkeeping heartbeat');
                }
            } catch (error) {
                console.error('[DC health] Bookkeeping heartbeat failed:', error?.message);
            } finally {
                globalThis.__dcReportHealth?.(this);
                this.scheduleHeartbeat(); // Even a failed or hung write must re-arm.
            }
        }, this.heartbeatIntervalMs());`);
fs.writeFileSync(file, text);
console.log('Installed bounded requests, self-rearming heartbeat, and local health reporting. Original backed up.');
