/*
 * MCP Ask AI — pushed page-context registry.
 *
 * Browser sends ordered IDs (+ optional scope_id parent selector) only.
 * Laravel re-authorizes and rebuilds fields. No digest/HMAC transport.
 */
(function () {
    'use strict';

    const ATTR_JOB_ID = 'data-mcp-context-job-id';
    const ATTR_OWNER = 'data-mcp-page-context-owner';
    const ATTR_SCOPE_ID = 'data-mcp-context-scope-id';
    const MAX_RECORDS = 100;

    /**
     * @typedef {Object} ClientPageContext
     * @property {number} version
     * @property {string} context_id
     * @property {string} route
     * @property {string} kind
     * @property {string} resource_type
     * @property {string[]} record_ids
     * @property {string} [scope_id]
     */

    function createRegistry() {
        /** @type {{ ownerId: string|null, generation: number, snapshot: ClientPageContext|null, suppression: Record<string, boolean>, morphInFlight: boolean }} */
        let state = {
            ownerId: null,
            generation: 0,
            snapshot: null,
            suppression: Object.create(null),
            morphInFlight: false,
        };

        /** @type {Array<(snap: ClientPageContext|null) => void>} */
        const subscribers = [];

        function notify() {
            const snap = state.snapshot;
            subscribers.slice().forEach((fn) => {
                try {
                    fn(snap);
                } catch (_) { /* ignore */ }
            });
        }

        function snapshot() {
            return state.snapshot ? Object.freeze(JSON.parse(JSON.stringify(state.snapshot))) : null;
        }

        function captureForSubmit() {
            if (state.morphInFlight) return null;
            const snap = snapshot();
            if (!snap) return null;
            if (state.suppression[snap.context_id]) return null;
            return snap;
        }

        function register(ownerId, clientContext) {
            if (!ownerId || !clientContext) return;
            if (state.suppression[clientContext.context_id]) {
                state.ownerId = ownerId;
                state.snapshot = null;
                notify();
                return;
            }
            state.ownerId = ownerId;
            state.generation += 1;
            state.snapshot = clientContext;
            notify();
        }

        function replace(ownerId, clientContext) {
            if (state.ownerId && state.ownerId !== ownerId) {
                return;
            }
            register(ownerId, clientContext);
        }

        function remove(ownerId) {
            if (ownerId && state.ownerId === ownerId) {
                state.ownerId = null;
                state.snapshot = null;
                notify();
            }
        }

        /** User Remove — tombstone keyed by context_id, not owner name. */
        function suppress(contextId) {
            if (!contextId) return;
            state.suppression[contextId] = true;
            if (state.snapshot?.context_id === contextId) {
                state.snapshot = null;
                notify();
            }
        }

        function clearOwnerOnNavigate() {
            if (state.ownerId) {
                const snap = state.snapshot;
                if (snap?.context_id) {
                    delete state.suppression[snap.context_id];
                }
            }
            state.ownerId = null;
            state.snapshot = null;
            state.morphInFlight = false;
            state.generation += 1;
            notify();
        }

        function setMorphInFlight(flag) {
            state.morphInFlight = !!flag;
        }

        function subscribe(fn) {
            subscribers.push(fn);
            return function unsubscribe() {
                const i = subscribers.indexOf(fn);
                if (i >= 0) subscribers.splice(i, 1);
            };
        }

        function debugState() {
            return {
                ownerId: state.ownerId,
                generation: state.generation,
                morphInFlight: state.morphInFlight,
                suppressed: Object.keys(state.suppression),
                snapshot: snapshot(),
            };
        }

        return {
            register,
            replace,
            remove,
            suppress,
            snapshot,
            captureForSubmit,
            clearOwnerOnNavigate,
            setMorphInFlight,
            subscribe,
            debugState,
        };
    }

    function collectJobIds(root, limit) {
        const cap = limit || MAX_RECORDS;
        const nodes = [];
        if (root && root.hasAttribute && root.hasAttribute(ATTR_JOB_ID)) {
            nodes.push(root);
        }
        if (root && root.querySelectorAll) {
            root.querySelectorAll('[' + ATTR_JOB_ID + ']').forEach((n) => nodes.push(n));
        }
        const ids = [];
        const seen = Object.create(null);
        nodes.forEach((el) => {
            if (ids.length >= cap) return;
            const raw = el.getAttribute(ATTR_JOB_ID);
            if (!raw) return;
            const parsed = parseInt(raw, 10);
            if (!Number.isFinite(parsed) || parsed < 1) return;
            const id = String(parsed);
            if (seen[id]) return;
            seen[id] = true;
            ids.push(id);
        });
        return ids;
    }

    function readOwnerMeta(ownerEl) {
        const contextId = ownerEl.getAttribute('data-mcp-context-id')
            || ('jobs:' + (ownerEl.id || 'root'));
        const scopeRaw = ownerEl.getAttribute(ATTR_SCOPE_ID);
        const versionRaw = ownerEl.getAttribute('data-mcp-context-version');
        const profile = ownerEl.getAttribute('data-mcp-context-profile');
        const version = versionRaw ? parseInt(versionRaw, 10) : (profile === 'jobs.show.v2' ? 2 : 1);
        const detailContract = ownerEl.getAttribute('data-mcp-context-detail-contract') || 'typed-detail.v2';
        const bundleHash = ownerEl.getAttribute('data-mcp-context-definition-bundle-hash')
            || (typeof document !== 'undefined' ? document.querySelector('meta[name="ai-definition-bundle-hash"]')?.getAttribute('content') : null)
            || '';
        const epochRaw = ownerEl.getAttribute('data-mcp-context-compatibility-epoch');
        const compatibilityEpoch = epochRaw ? parseInt(epochRaw, 10) : 2;

        const meta = {
            ownerId: ownerEl.getAttribute(ATTR_OWNER) || 'jobs',
            route: ownerEl.getAttribute('data-mcp-context-route') || 'work-order.index',
            kind: ownerEl.getAttribute('data-mcp-context-kind') || 'list',
            resourceType: ownerEl.getAttribute('data-mcp-context-resource') || 'work_order',
            contextId,
            version: Number.isFinite(version) ? version : 1,
            profile: profile || (version === 2 ? 'jobs.show.v2' : null),
            detailContract,
            definitionBundleHash: bundleHash,
            compatibilityEpoch: Number.isFinite(compatibilityEpoch) ? compatibilityEpoch : 2,
            scopeId: scopeRaw && String(parseInt(scopeRaw, 10)) !== 'NaN'
                ? String(parseInt(scopeRaw, 10))
                : null,
        };
        return meta;
    }

    function buildClientContext(ownerEl) {
        const meta = readOwnerMeta(ownerEl);
        const jobIds = collectJobIds(ownerEl, MAX_RECORDS);

        if (meta.version === 2 || meta.profile === 'jobs.show.v2') {
            const resourceType = (meta.resourceType === 'work_order' || !meta.resourceType) ? 'job' : meta.resourceType;
            const records = jobIds.map((id) => ({
                id: String(id),
                resource_type: resourceType,
            }));

            return {
                version: 2,
                profile: meta.profile || 'jobs.show.v2',
                records,
                detail_contract: meta.detailContract,
                definition_bundle_hash: meta.definitionBundleHash,
                compatibility_epoch: meta.compatibilityEpoch,
            };
        }

        /** @type {ClientPageContext} */
        const ctx = {
            version: meta.version,
            context_id: meta.contextId,
            route: meta.route,
            kind: meta.kind,
            resource_type: meta.resourceType,
            record_ids: jobIds,
        };
        if (meta.scopeId) {
            ctx.scope_id = meta.scopeId;
        }
        return ctx;
    }

    function findOwners() {
        if (typeof document === 'undefined') return [];
        return Array.from(document.querySelectorAll('[' + ATTR_OWNER + ']'));
    }

    function syncActiveOwner(registry) {
        const owners = findOwners();
        if (owners.length === 0) {
            if (registry.debugState().ownerId) {
                registry.remove(registry.debugState().ownerId);
            }
            return;
        }
        if (owners.length > 1) {
            registry.remove(registry.debugState().ownerId);
            if (typeof process !== 'undefined' && process.env?.NODE_ENV === 'test') {
                // Vitest: deterministic single-owner tests only.
            } else if (typeof window !== 'undefined' && window.location?.hostname === 'localhost') {
                document.dispatchEvent(new CustomEvent('mcp:page-context:multiple-owners', {
                    detail: { count: owners.length },
                }));
            }
            return;
        }
        const owner = owners[0];
        const meta = readOwnerMeta(owner);
        registry.replace(meta.ownerId, buildClientContext(owner));
    }

    function bootPageContext(registry) {
        syncActiveOwner(registry);

        document.addEventListener('livewire:navigated', () => {
            registry.clearOwnerOnNavigate();
            syncActiveOwner(registry);
        });

        window.addEventListener('pagehide', () => {
            registry.clearOwnerOnNavigate();
        });

        /** @type {MutationObserver|null} */
        let domObserver = null;

        const startDomObserver = () => {
            if (domObserver || typeof MutationObserver === 'undefined') return;
            let debounceTimer = null;
            domObserver = new MutationObserver(() => {
                if (typeof document === 'undefined') return;
                if (registry.debugState().morphInFlight) return;
                if (debounceTimer) clearTimeout(debounceTimer);
                debounceTimer = setTimeout(() => {
                    debounceTimer = null;
                    if (registry.debugState().morphInFlight) return;
                    syncActiveOwner(registry);
                }, 50);
            });
            domObserver.observe(document.body, { childList: true, subtree: true });
        };

        const stopDomObserver = () => {
            if (!domObserver) return;
            domObserver.disconnect();
            domObserver = null;
        };

        const hookLivewire = () => {
            if (!window.Livewire || typeof window.Livewire.hook !== 'function') return false;

            stopDomObserver();

            window.Livewire.hook('commit', ({ succeed, fail }) => {
                registry.setMorphInFlight(true);
                succeed(() => {
                    registry.setMorphInFlight(false);
                    syncActiveOwner(registry);
                });
                fail(() => {
                    registry.setMorphInFlight(false);
                });
            });

            return true;
        };

        if (!hookLivewire()) {
            startDomObserver();
            document.addEventListener('livewire:init', () => {
                hookLivewire();
            }, { once: true });
        }
    }

    const registry = createRegistry();

    function boot() {
        bootPageContext(registry);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }

    const api = {
        PageContextRegistry: registry,
        collectJobIds,
        buildClientContext,
        createRegistry,
        readOwnerMeta,
        findOwners,
        bootPageContext,
        syncActiveOwner,
        ATTR_JOB_ID,
        ATTR_OWNER,
        ATTR_SCOPE_ID,
        MAX_RECORDS,
    };

    window.McpPageContext = api;

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})();
