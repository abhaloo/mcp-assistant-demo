/**
 * Ask AI v2 entry point.
 *
 * v2 is a transport and a set of optional surfaces, not a panel. The panel is
 * the legacy Ask AI surface, which keeps the thread, composer, page context,
 * record chips, follow-ups, copy/regenerate and the trust affordances in every
 * mode. This file registers the v2 protocol adapter so the panel can talk to
 * the v2 stream, then mounts whichever v2 modules the deployment enables.
 *
 * Turning every module off leaves a working panel on the v2 transport.
 */

import { askV2Protocol, askV2Bus, resetProjectionQueues } from './ask_v2_protocol.js';
import { workTimelineModule } from './modules/work_timeline.js';
import { resultTableModule, copyTextFor } from './modules/result_table.js';
import { interactionCardModule } from './modules/interaction_card.js';
import { thoughtStreamModule, quarantineThoughtCache, releaseThoughtCache } from './modules/thought_stream.js';
import { sourceRestoreModule, sourceRestoreSlice } from './modules/source_restore.js';
import { turnStore } from './turn_state.js';
import { installRestoreGate } from './restore_client.js';
import { slotElement } from './modules/module_host.js';
import {
    activeModules,
    applyModuleFlags,
    disableAllModules,
    disableModule,
    enableModule,
    registerModule,
    registeredModules,
} from './modules/registry.js';

registerModule(workTimelineModule);
registerModule(resultTableModule);
registerModule(interactionCardModule);
registerModule(thoughtStreamModule);
registerModule(sourceRestoreModule);
turnStore.registerRestoreSlice('source_restore', sourceRestoreSlice);


/** Reads the module flags the server rendered onto the panel root. */
function moduleFlags(root) {
    try {
        return JSON.parse(root.dataset.v2Modules || '{}');
    } catch (_) {
        // A malformed flag set must not decide the surface by accident. No
        // modules is a working panel; half-parsed flags are not.
        return {};
    }
}

function wrapRestoreHooks() {
    const currentApply = window.McpAskAiRestoreApply;
    if (typeof currentApply !== 'function' || !currentApply.__mcpAskV2Restore) {
        const wrappedApply = (data) => {
            turnStore.applyAuthorizedRestore?.(data);
            releaseThoughtCache();
            if (typeof currentApply === 'function' && !currentApply.__mcpAskV2Restore) {
                currentApply(data);
            }
        };
        wrappedApply.__mcpAskV2Restore = true;
        window.McpAskAiRestoreApply = wrappedApply;
    }
    const currentClear = window.McpAskAiRestoreClear;
    if (typeof currentClear !== 'function' || !currentClear.__mcpAskV2Restore) {
        const wrappedClear = () => {
            quarantineThoughtCache();
            if (typeof currentClear === 'function' && !currentClear.__mcpAskV2Restore) {
                currentClear();
            }
        };
        wrappedClear.__mcpAskV2Restore = true;
        window.McpAskAiRestoreClear = wrappedClear;
    }
}

function boot() {
    const root = document.getElementById('mcp-ask-ai-root');
    if (!root || root.dataset.v2Enabled !== '1') {
        return;
    }

    installRestoreGate(window);
    wrapRestoreHooks();

    // The panel resolves its protocol through this on every send.
    window.McpAskAiProtocols = {
        active: () => askV2Protocol,
        v2: askV2Protocol,
    };

    // Copy reads the painted result first, then the prose; never the hidden fallback text.
    window.McpAskAiCopyText = (msgId) => {
        const prose = slotElement(msgId, 'answer')?.textContent?.trim() || '';
        return [copyTextFor(msgId), prose].filter(Boolean).join('\n\n') || null;
    };

    const mountModules = () => {
        wrapRestoreHooks();
        const flags = moduleFlags(root);
        // Compatible consumer: mount the citation slice unless the panel flag
        // explicitly turns it off. Dataset v2Modules.source_restore is the
        // capability seam; a missing key is not treated as a successful HTTP.
        if (flags.source_restore === undefined) {
            flags.source_restore = true;
        }
        applyModuleFlags(flags, askV2Bus);
    };
    mountModules();

    // Modules paint into the open panel, so they stop when it closes and remount when it opens.
    window.McpAskAiSidebar?.setHooks?.({
        onOpen: mountModules,
        onClosing: () => {
            window.McpAskAiRestoreGate?.invalidate();
            resetProjectionQueues();
            disableAllModules();
        },
    });

    // Lets an operator switch one surface off in a live session without a
    // deploy, which is how a suspect module gets isolated.
    window.McpAskAiModules = {
        list: registeredModules,
        active: activeModules,
        enable: (name) => enableModule(name, askV2Bus),
        disable: disableModule,
    };
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
} else {
    boot();
}
