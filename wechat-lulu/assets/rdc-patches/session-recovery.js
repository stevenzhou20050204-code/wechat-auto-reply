// Background recovery must not confuse an unavailable auth server with a
// rejected login, nor silently start a fresh browser authorization flow.
const TERMINAL_AUTH_CODES = new Set([
    'refresh_token_not_found', 'refresh_token_already_used',
    'invalid_refresh_token', 'session_not_found', 'session_expired',
    'user_not_found', 'user_banned', 'invalid_grant',
]);

export function classifyRestoreError(error) {
    for (let current = error, depth = 0; current && depth < 5; current = current.cause, depth++) {
        if (TERMINAL_AUTH_CODES.has(current.code) &&
            (current.status === 400 || current.status === 401 || current.status === 403)) {
            return 'authentication-required';
        }
    }
    // Timeouts, resets, rate limits, server failures and ambiguous responses
    // preserve the saved session. None proves that credentials were revoked.
    return 'session-restore-unavailable';
}

export async function restoreSavedSession(setSession, session, onStage = () => {}) {
    let failure;
    try {
        const result = await setSession(session);
        failure = result?.error;
        if (!failure) return result;
    } catch (error) {
        failure = error;
    }
    const stage = classifyRestoreError(failure);
    onStage(stage);
    const error = new Error(stage === 'authentication-required'
        ? 'Saved RDC login was explicitly rejected; owner authorization is required.'
        : 'RDC session restore unavailable; saved login retained for background recovery.');
    error.code = stage === 'authentication-required' ? 'RDC_AUTH_REJECTED' : 'RDC_RESTORE_UNAVAILABLE';
    // Keep tokens and original error text out of startup telemetry/logs.
    throw error;
}

export function requireInteractiveAuthorization(env = process.env) {
    if (env.DC_NONINTERACTIVE === '1') {
        globalThis.__dcStartupStage?.('authentication-required');
        const error = new Error('Fresh RDC authorization requires the owner; background service will not open a browser.');
        error.code = 'RDC_INTERACTIVE_AUTH_REQUIRED';
        throw error;
    }
}
