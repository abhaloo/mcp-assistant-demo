/**
 * Evidence restore gate.
 *
 * Enforces the "never paint first, then remove denied content" invariant from
 * ADR 0076 / Spec §7:
 *   - Captures a monotonic generation token at the START of restore().
 *   - Fetches server-authorized evidence via fetchEvidence().
 *   - Rejects the result (never calls applyAuthorized) if invalidate() was
 *     called during the fetch — this covers new-chat, user-switch, live-turn,
 *     and any other event that invalidates the session.
 *   - Rejects if the identity (userId/entityId/threadId) changed.
 *
 * The gate never hands unverified cached content to the DOM. All cached reads
 * must be quarantined before restore() is called; applyAuthorized is the only
 * path that releases server-returned content to the display layer.
 *
 * @example
 * const gate = createRestoreGate({ fetchEvidence, applyAuthorized, clearRestored });
 * gate.invalidate();          // quarantine; called before every new-chat / re-open
 * await gate.restore(identity, refs);  // fetches + applies iff not invalidated
 */

/**
 * @typedef {Object} RestoreIdentity
 * @property {string|number} userId
 * @property {string|number|null} entityId
 * @property {string} threadId
 */

/**
 * @typedef {'idle'|'checking'|'ready'} GateState
 */

/**
 * Create a restore gate.
 *
 * @param {Object} opts
 * @param {Function} opts.fetchEvidence  - (threadId, refs) => Promise<{results}>
 * @param {Function} opts.applyAuthorized - (turn) => void
 * @param {Function} opts.clearRestored   - () => void
 * @returns {{ restore: Function, invalidate: Function, state: Function }}
 */
export function createRestoreGate({ fetchEvidence, applyAuthorized, clearRestored }) {
    let _generation = 0;
    let _currentIdentity = null;
    let _state = 'idle';

    function identityKey(identity) {
        if (!identity) return null;
        return `${identity.userId}:${identity.entityId}:${identity.threadId}`;
    }

    /**
     * Increment the generation token. Any in-flight restore() that captured
     * an older token will silently abort before calling applyAuthorized.
     */
    function invalidate() {
        _generation += 1;
        _currentIdentity = null;
        _state = 'idle';
        try {
            clearRestored();
        } catch (_) {}
    }

    /**
     * Fetch server-authorized evidence and apply it iff not invalidated.
     *
     * @param {RestoreIdentity} identity
     * @param {string[]} references - Array of restore_ref strings.
     * @returns {Promise<void>}
     */
    async function restore(identity, references) {
        if (!identity || !references || references.length === 0) {
            return;
        }

        // Capture state at the start of this call. If invalidate() fires
        // during the await below, the token will have advanced and we abort.
        const capturedGeneration = _generation;
        const capturedIdentityKey = identityKey(identity);
        _currentIdentity = identity;
        _state = 'checking';

        try {
            const data = await fetchEvidence(identity.threadId, references);

            // Stale-restore guard: abort if invalidated or identity changed.
            if (_generation !== capturedGeneration) {
                return;
            }
            if (identityKey(_currentIdentity) !== capturedIdentityKey) {
                return;
            }

            // Ready before apply: the render modules repaint from the applied
            // payload, and they skip repaints while the gate is checking.
            _state = 'ready';
            if (data && Array.isArray(data.results) && data.results.length > 0) {
                try {
                    applyAuthorized(data);
                } catch (_) {}
            }
        } catch (_) {
            // Network/auth failure: never apply unverified content.
            if (_generation === capturedGeneration) {
                _state = 'idle';
            }
        }
    }

    /**
     * @returns {GateState}
     */
    function state() {
        return _state;
    }

    return { restore, invalidate, state };
}
