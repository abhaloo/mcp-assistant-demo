import { describe, it, expect, beforeEach } from 'vitest';

import {

    createRegistry,

    collectJobIds,

    bootPageContext,

    ATTR_JOB_ID,

    ATTR_OWNER,

} from '../../public/js/mcp-page-context.js';



describe('PageContextRegistry lifecycle', () => {

    let registry;



    beforeEach(() => {

        registry = createRegistry();

        document.body.innerHTML = '';

    });



    it('registers a snapshot and notifies subscribers', () => {

        const seen = [];

        registry.subscribe((snap) => seen.push(snap));



        registry.register('jobs', {

            version: 1,

            context_id: 'jobs:1',

            route: 'work-order.index',

            kind: 'list',

            resource_type: 'work_order',

            record_ids: ['10', '11'],

        });



        expect(registry.snapshot()?.record_ids).toEqual(['10', '11']);

        expect(seen.at(-1)?.record_ids).toEqual(['10', '11']);

    });



    it('suppress tombstone blocks re-register until navigate clear', () => {

        registry.register('jobs', {

            version: 1,

            context_id: 'jobs:1',

            route: 'work-order.index',

            kind: 'list',

            resource_type: 'work_order',

            record_ids: ['1'],

        });

        registry.suppress('jobs:1');

        expect(registry.snapshot()).toBeNull();

        expect(registry.captureForSubmit()).toBeNull();



        registry.register('jobs', {

            version: 1,

            context_id: 'jobs:1',

            route: 'work-order.index',

            kind: 'list',

            resource_type: 'work_order',

            record_ids: ['1', '2'],

        });

        expect(registry.snapshot()).toBeNull();



        registry.clearOwnerOnNavigate();

        registry.register('jobs', {

            version: 1,

            context_id: 'jobs:2',

            route: 'work-order.index',

            kind: 'list',

            resource_type: 'work_order',

            record_ids: ['9'],

        });

        expect(registry.snapshot()?.record_ids).toEqual(['9']);

    });



    it('captureForSubmit returns null while morph is in flight', () => {

        registry.register('jobs', {

            version: 1,

            context_id: 'jobs:1',

            route: 'work-order.index',

            kind: 'list',

            resource_type: 'work_order',

            record_ids: ['1'],

        });

        registry.setMorphInFlight(true);

        expect(registry.captureForSubmit()).toBeNull();

        registry.setMorphInFlight(false);

        expect(registry.captureForSubmit()?.record_ids).toEqual(['1']);

    });



    it('owner remove drops snapshot without clearing suppression', () => {

        registry.suppress('jobs:1');

        registry.register('jobs', {

            version: 1,

            context_id: 'jobs:1',

            route: 'work-order.index',

            kind: 'list',

            resource_type: 'work_order',

            record_ids: ['1'],

        });

        registry.remove('jobs');

        expect(registry.debugState().suppressed).toContain('jobs:1');

        expect(registry.snapshot()).toBeNull();

    });



    it('rejects multiple owners by clearing active snapshot', () => {
        document.body.innerHTML = `
            <div ${ATTR_OWNER}="jobs" data-mcp-context-id="a" data-mcp-context-route="work-order.index"
                 data-mcp-context-kind="list" data-mcp-context-resource="work_order">
                <span ${ATTR_JOB_ID}="1"></span>
            </div>
            <div ${ATTR_OWNER}="jobs" data-mcp-context-id="b" data-mcp-context-route="work-order.show"
                 data-mcp-context-kind="detail" data-mcp-context-resource="work_order">
                <span ${ATTR_JOB_ID}="2"></span>
            </div>
        `;
        const mod = require('../../public/js/mcp-page-context.js');
        expect(mod.findOwners().length).toBe(2);

        // Register one owner, then syncActiveOwner must clear because two owners exist.
        registry.register('jobs', mod.buildClientContext(mod.findOwners()[0]));
        expect(registry.snapshot()?.record_ids).toEqual(['1']);

        mod.syncActiveOwner(registry);
        expect(registry.snapshot()).toBeNull();
        expect(registry.debugState().ownerId).toBeNull();
    });

    it('isolates Remove tombstones by context_id', () => {
        registry.register('jobs', {
            version: 1,
            context_id: 'jobs:bill:1',
            route: 'bill.show',
            kind: 'embedded_list',
            resource_type: 'work_order',
            scope_id: '1',
            record_ids: ['1'],
        });
        registry.suppress('jobs:bill:1');
        registry.clearOwnerOnNavigate();
        registry.register('jobs', {
            version: 1,
            context_id: 'jobs:show:2',
            route: 'work-order.show',
            kind: 'detail',
            resource_type: 'work_order',
            record_ids: ['2'],
        });
        expect(registry.captureForSubmit()?.context_id).toBe('jobs:show:2');
    });

    it('does not publish mid-morph snapshots while Livewire commit is in flight', () => {
        const localRegistry = createRegistry();
        let commitHook = null;

        window.Livewire = {
            hook(name, fn) {
                if (name === 'commit') commitHook = fn;
            },
        };

        document.body.innerHTML = `
            <div ${ATTR_OWNER}="jobs" data-mcp-context-id="jobs:1" data-mcp-context-route="work-order.index"
                 data-mcp-context-kind="list" data-mcp-context-resource="work_order">
                <span ${ATTR_JOB_ID}="1"></span>
            </div>
        `;

        bootPageContext(localRegistry);
        expect(localRegistry.snapshot()?.record_ids).toEqual(['1']);

        document.querySelector(`[${ATTR_JOB_ID}]`).setAttribute(ATTR_JOB_ID, '99');
        expect(localRegistry.snapshot()?.record_ids).toEqual(['1']);

        expect(commitHook).toBeTypeOf('function');
        let finishCommit = null;
        commitHook({
            succeed(fn) {
                finishCommit = fn;
            },
            fail() {},
        });

        document.querySelector(`[${ATTR_JOB_ID}]`).setAttribute(ATTR_JOB_ID, '42');
        expect(localRegistry.captureForSubmit()).toBeNull();
        expect(localRegistry.snapshot()?.record_ids).toEqual(['1']);

        finishCommit();
        expect(localRegistry.snapshot()?.record_ids).toEqual(['42']);
        expect(localRegistry.captureForSubmit()?.record_ids).toEqual(['42']);

        delete window.Livewire;
    });

});



describe('collectJobIds', () => {
    it('dedupes first-occurrence across desktop and mobile nodes', () => {
        document.body.innerHTML = `
            <div ${ATTR_OWNER}="jobs">
                <tr ${ATTR_JOB_ID}="5"></tr>
                <tr ${ATTR_JOB_ID}="7"></tr>
                <div ${ATTR_JOB_ID}="5"></div>
                <div ${ATTR_JOB_ID}="7"></div>
                <div ${ATTR_JOB_ID}="9"></div>
            </div>
        `;
        const root = document.querySelector(`[${ATTR_OWNER}="jobs"]`);
        expect(collectJobIds(root)).toEqual(['5', '7', '9']);
    });
});

describe('Cycle 2 & Cycle 9: Real Job Page Producer Payload (v1 and v2)', () => {
    let registry;

    beforeEach(() => {
        registry = createRegistry();
        document.body.innerHTML = '';
    });

    it('renders jobs.show with one owner producing exactly accepted v1 tuple', () => {
        document.body.innerHTML = `
            <div ${ATTR_OWNER}="jobs"
                 data-mcp-context-route="work-order.show"
                 data-mcp-context-kind="detail"
                 data-mcp-context-resource="work_order"
                 data-mcp-context-id="jobs:show:24694"
                 data-mcp-context-job-id="24694">
                <span ${ATTR_JOB_ID}="24694"></span>
            </div>
        `;
        const mod = require('../../public/js/mcp-page-context.js');
        const owners = mod.findOwners();
        expect(owners.length).toBe(1);

        const ctx = mod.buildClientContext(owners[0]);
        expect(ctx).toEqual({
            version: 1,
            context_id: 'jobs:show:24694',
            route: 'work-order.show',
            kind: 'detail',
            resource_type: 'work_order',
            record_ids: ['24694'],
        });

        mod.syncActiveOwner(registry);
        expect(registry.snapshot()).toEqual(ctx);
        expect(registry.captureForSubmit()).toEqual(ctx);
    });

    it('fails closed with zero owners or duplicated owners on jobs.show', () => {
        const mod = require('../../public/js/mcp-page-context.js');

        // Zero owners
        document.body.innerHTML = '<div>No owners here</div>';
        mod.syncActiveOwner(registry);
        expect(registry.snapshot()).toBeNull();
        expect(registry.captureForSubmit()).toBeNull();

        // Multiple owners
        document.body.innerHTML = `
            <div ${ATTR_OWNER}="jobs" data-mcp-context-route="work-order.show" data-mcp-context-kind="detail" data-mcp-context-resource="work_order">
                <span ${ATTR_JOB_ID}="24694"></span>
            </div>
            <div ${ATTR_OWNER}="jobs" data-mcp-context-route="work-order.show" data-mcp-context-kind="detail" data-mcp-context-resource="work_order">
                <span ${ATTR_JOB_ID}="24695"></span>
            </div>
        `;
        mod.syncActiveOwner(registry);
        expect(registry.snapshot()).toBeNull();
        expect(registry.captureForSubmit()).toBeNull();
    });

    it('emits v2 identity-only payload for jobs.show.v2', () => {
        const bundleHash = 'sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef';
        document.body.innerHTML = `
            <div ${ATTR_OWNER}="jobs"
                 data-mcp-context-version="2"
                 data-mcp-context-profile="jobs.show.v2"
                 data-mcp-context-detail-contract="typed-detail.v2"
                 data-mcp-context-definition-bundle-hash="${bundleHash}"
                 data-mcp-context-compatibility-epoch="2"
                 data-mcp-context-id="jobs:show:24694"
                 data-mcp-context-job-id="24694">
                <span ${ATTR_JOB_ID}="24694"></span>
            </div>
        `;
        const mod = require('../../public/js/mcp-page-context.js');
        const owners = mod.findOwners();
        expect(owners.length).toBe(1);

        const ctx = mod.buildClientContext(owners[0]);
        expect(ctx).toEqual({
            version: 2,
            profile: 'jobs.show.v2',
            records: [{ id: '24694', resource_type: 'job' }],
            detail_contract: 'typed-detail.v2',
            definition_bundle_hash: bundleHash,
            compatibility_epoch: 2,
        });

        mod.syncActiveOwner(registry);
        expect(registry.captureForSubmit()).toEqual(ctx);
    });

    it('v2 lifecycle: unmount/navigation clears stale context and morph cannot resurrect detached owner', () => {
        const bundleHash = 'sha256:abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789';
        document.body.innerHTML = `
            <div id="job-root" ${ATTR_OWNER}="jobs"
                 data-mcp-context-version="2"
                 data-mcp-context-profile="jobs.show.v2"
                 data-mcp-context-detail-contract="typed-detail.v2"
                 data-mcp-context-definition-bundle-hash="${bundleHash}"
                 data-mcp-context-compatibility-epoch="2">
                <span ${ATTR_JOB_ID}="100"></span>
            </div>
        `;
        const mod = require('../../public/js/mcp-page-context.js');
        mod.syncActiveOwner(registry);
        expect(registry.captureForSubmit()?.records).toEqual([{ id: '100', resource_type: 'job' }]);

        // Navigation / unmount clears owner
        registry.clearOwnerOnNavigate();
        expect(registry.snapshot()).toBeNull();
        expect(registry.captureForSubmit()).toBeNull();

        // During morph in flight
        registry.setMorphInFlight(true);
        expect(registry.captureForSubmit()).toBeNull();

        // Detach owner from DOM
        document.getElementById('job-root').remove();
        registry.setMorphInFlight(false);
        mod.syncActiveOwner(registry);
        expect(registry.snapshot()).toBeNull();
        expect(registry.captureForSubmit()).toBeNull();
    });
});


