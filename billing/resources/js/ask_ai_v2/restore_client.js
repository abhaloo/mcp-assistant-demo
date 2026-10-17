import { createRestoreGate } from './restore_gate.js';

/**
 * Evidence restore client.
 *
 * Sends a POST to /ai-search/restore-evidence and validates the R1/R2
 * contract response. Callers must hold a scope=restore_evidence JWT (minted
 * by the server per-request via RagClient::restoreEvidence).
 *
 * This module never stores, logs, or echoes the server payload — it hands it
 * directly to the restore gate which is the only component allowed to apply it.
 */

/**
 * Fetch restored evidence for one thread.
 *
 * @param {string} threadId   - The thread identifier.
 * @param {Array<string>} references - Array of restore_ref strings.
 * @returns {Promise<{version: number, results: Array}>}
 */
export async function fetchRestoredEvidence(threadId, references) {
    if (!threadId || !Array.isArray(references) || references.length === 0) {
        throw new TypeError('fetchRestoredEvidence: threadId and non-empty references required');
    }

    const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';

    const body = {
        thread_id: threadId,
        references: references.map((r) => ({ restore_ref: r })),
    };

    const response = await fetch('/ai-search/restore-evidence', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'X-CSRF-TOKEN': csrf,
        },
        credentials: 'same-origin',
        body: JSON.stringify(body),
    });

    if (!response.ok) {
        const err = new Error(`restore-evidence: HTTP ${response.status}`);
        err.status = response.status;
        throw err;
    }

    const data = await response.json();

    // Validate the R1/R2 response contract
    if (data.version !== 1 || !Array.isArray(data.results)) {
        throw new Error('restore-evidence: unexpected response schema');
    }

    return data;
}

/**
 * Attach the shared gate on window for IIFE panel scripts.
 *
 * @param {Window} target
 * @returns {ReturnType<typeof createRestoreGate>|undefined}
 */
export function installRestoreGate(target = typeof window !== 'undefined' ? window : undefined) {
    if (!target) return undefined;
    if (target.McpAskAiRestoreGate) return target.McpAskAiRestoreGate;
    target.McpAskAiRestoreGate = createRestoreGate({
        fetchEvidence: fetchRestoredEvidence,
        applyAuthorized: (data) => target.McpAskAiRestoreApply?.(data),
        clearRestored: () => target.McpAskAiRestoreClear?.(),
    });
    return target.McpAskAiRestoreGate;
}

if (typeof window !== 'undefined') {
    installRestoreGate(window);
}
