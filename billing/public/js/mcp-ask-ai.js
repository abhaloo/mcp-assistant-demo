/*
 * MCP Ask AI — push-sidebar chat panel (thread state, composer, feedback).
 */
(function () {
    'use strict';

    const Persist = window.McpAskAiPersist;
    const StreamUi = window.McpAskAiStreamUi;
    const Feedback = window.McpAskAiFeedback;
    const StreamClient = window.McpAskStreamClient;
    if (!Persist) {
        throw new Error('mcp-ask-ai.js requires mcp-ask-ai-persist.js to be loaded first');
    }
    if (!StreamUi) {
        throw new Error('mcp-ask-ai.js requires mcp-ask-ai-stream-ui.js to be loaded first');
    }
    if (!Feedback) {
        throw new Error('mcp-ask-ai.js requires mcp-ask-ai-feedback.js to be loaded first');
    }
    if (!StreamClient) {
        throw new Error('mcp-ask-ai.js requires mcp-ask-stream-client.js to be loaded first');
    }

    const MIN_QUERY = 2;
    const MAX_QUESTION = 2000;
    const REVEAL_DIVISOR = 12;
    const MAX_LAG_CHARS = 400;
    const NEUTRAL_STATUS_DELAY_MS = 4000;
    const COPIED_MS = 1500;
    // Send turns into Stop on the click that sends; a click this soon after is
    // the same gesture, never a Stop.
    const STOP_ARM_DELAY_MS = 400;

    const {
        PHASE_GROUPS,
        deepCloneFreeze,
        contextLabel,
        makeRetryDescriptor,
        loaderStageForMsg,
        loaderSrcForMsg,
        displayLabelForMsg,
    } = StreamUi;

    const EMPTY_PROMPTS = [];

    let root = null;
    let threadEl = null;
    let textarea = null;
    let counterEl = null;
    let sendBtn = null;
    let streamUrl = '';
    let feedbackUrl = '';
    let timingUrl = '';
    let cancelUrl = '';
    let ragEnabled = false;
    let starterPrompts = [];

    let currentThreadId = null;
    // Task B7 / R4: explicit UI states replacing the old nullable multiTurn
    // inference — 'new' (blank slate) | 'resuming' (thread restored from
    // sessionStorage, capability not yet reconfirmed this session — a
    // RECONNECT, see R5) | 'ready' (server-confirmed follow-up capable) |
    // 'independent' (server-confirmed single-turn) | 'error' (last turn failed).
    let threadState = 'new';
    let messages = [];
    // Set when a thread past the panel window was cleared on load; a new chat resets it.
    let threadClearedNotice = false;
    let streamAbort = null;
    let stopArmedAt = 0;
    let srBatch = null;
    let userScrolledUp = false;
    let msgId = 0;
    let activeRunId = null;
    let activeTurnId = 0;
    const statusTimers = new Map();
    const elapsedTimers = new Map();
    const copiedTimers = new Map();

    let ui = null;
    let feedback = null;

    function claimTurn() {
        return ++activeTurnId;
    }

    function isActiveTurn(turnId) {
        return turnId === activeTurnId;
    }

    const CRYPTO_UNAVAILABLE_USER_MESSAGE =
        'Ask Assistant requires a secure browser context. Use HTTPS or a browser with Web Crypto available.';

    function randomRunId() {
        // crypto.getRandomValues is required: it is the only source of
        // unpredictable, collision-resistant bytes here. The old optional
        // chaining (`window.crypto?.getRandomValues?.(bytes)`) silently no-op'd
        // when unavailable, leaving `bytes` at its zero-filled initial state —
        // a truthy, valid-looking-but-all-zero id every affected client would
        // collide on. Fail loudly instead of minting a bad id.
        if (typeof window.crypto?.getRandomValues !== 'function') {
            const err = new Error(
                'randomRunId: crypto.getRandomValues is unavailable; cannot mint a secure correlation ID.'
            );
            err.name = 'CryptoUnavailableError';
            throw err;
        }
        const bytes = new Uint8Array(16);
        window.crypto.getRandomValues(bytes);
        return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
    }

    function normalizeFollowupSuggestions(raw) {
        return Persist.normalizeFollowupSuggestions(raw);
    }

    // Resolves after two frames so the loader is on screen before the stream
    // moves on. A tab that goes hidden while those frames are pending never
    // paints them, so hiding releases the wait instead of holding the turn.
    function waitForLoaderPaint() {
        if (document.visibilityState === 'hidden'
            || typeof window.requestAnimationFrame !== 'function') {
            return Promise.resolve();
        }
        return new Promise((resolve) => {
            let settled = false;
            const finish = () => {
                if (settled) return;
                settled = true;
                document.removeEventListener('visibilitychange', onHide);
                resolve();
            };
            const onHide = () => {
                if (document.visibilityState === 'hidden') finish();
            };
            document.addEventListener('visibilitychange', onHide);
            window.requestAnimationFrame(() => window.requestAnimationFrame(finish));
        });
    }

    function activeContextSnapshot() {
        return window.McpPageContext?.PageContextRegistry?.captureForSubmit?.() || null;
    }

    // Copy with a graceful fallback: the async Clipboard API needs a secure context
    // (https / localhost); on plain-HTTP deployments fall back to execCommand so the
    // copy button is never a silent dead end.
    function copyTextToClipboard(text) {
        if (navigator.clipboard?.writeText) {
            return navigator.clipboard.writeText(text);
        }
        return new Promise((resolve, reject) => {
            try {
                const ta = document.createElement('textarea');
                ta.value = text;
                ta.setAttribute('readonly', '');
                ta.style.position = 'fixed';
                ta.style.opacity = '0';
                document.body.appendChild(ta);
                ta.select();
                const ok = document.execCommand('copy');
                document.body.removeChild(ta);
                ok ? resolve() : reject(new Error('copy command rejected'));
            } catch (err) {
                reject(err);
            }
        });
    }

    function stopElapsedTimer(assistant) {
        const handle = elapsedTimers.get(assistant?.id);
        if (handle) clearInterval(handle);
        if (assistant?.id) elapsedTimers.delete(assistant.id);
    }

    // Count-up seconds shown in the streaming status while the user waits (Issue 4).
    // Deliberately NOT folded into the neutral-status timer: that one is cleared on
    // every phase/token event (onStatus/onToken), which would freeze this counter
    // mid-wait. The paint self-terminates once the wait is over — the loading block
    // (and its span) is gone, content has started, or streaming ended — so it needs
    // no explicit stop at each terminal site; start is wired at the turn boundaries.
    function startElapsedTimer(assistant) {
        stopElapsedTimer(assistant);
        if (!assistant?.id) return;
        const startedAt = Date.now();
        const paint = () => {
            const el = root?.querySelector(
                '[data-msg-id="' + assistant.id + '"] .mcp-ask-ai__elapsed'
            );
            if (!el || !assistant.streaming || String(assistant.content || '').trim()) {
                stopElapsedTimer(assistant);
                return;
            }
            el.textContent = Math.floor((Date.now() - startedAt) / 1000) + 's';
        };
        paint();
        elapsedTimers.set(assistant.id, setInterval(paint, 1000));
    }

    function clearNeutralStatusTimer(assistant) {
        const timer = statusTimers.get(assistant?.id);
        if (timer) clearTimeout(timer);
        if (assistant?.id) statusTimers.delete(assistant.id);
    }

    function startNeutralStatusTimer(assistant, srEl = null) {
        clearNeutralStatusTimer(assistant);
        const timer = setTimeout(() => {
            statusTimers.delete(assistant.id);
            const latest = assistant.phases?.at(-1)?.phase;
            if (!assistant.streaming || String(assistant.content || '').trim() || latest !== 'understanding') return;
            // A module that owns this turn's progress display speaks for it;
            // pushing the neutral label would resurface the removed badge.
            const msgEl = document.querySelector('[data-msg-id="' + assistant.id + '"]');
            if (msgEl?.getAttribute('data-progress-owner') === 'module') return;
            const phase = { phase: 'working', label: 'Working on your request' };
            assistant.phases.push(phase);
            updateStreamingStatusUI(assistant);
            if (srEl) srEl.textContent = phase.label;
        }, NEUTRAL_STATUS_DELAY_MS);
        statusTimers.set(assistant.id, timer);
    }

    function clearPersistedThread(key) {
        Persist.clearPersistedThread(key);
    }

    function persistThread() {
        Persist.persistThread({ threadId: currentThreadId, threadState, messages });
    }

    function restoreThread() {
        const saved = Persist.loadPersistedThread(deepCloneFreeze);
        if (!saved) return;
        if (Persist.threadExpired(saved, Date.now())) {
            // Past the panel window: start empty. The server keeps the old thread.
            clearPersistedThread();
            threadClearedNotice = true;
            updateModeLabel();
            return;
        }
        currentThreadId = saved.threadId;
        msgId = saved.msgId;
        const refs = saved.messages.map((m) => m.restore_ref).filter(Boolean);
        const gate = window.McpAskAiRestoreGate;
        const paint = () => {
            if (!messages.length) return;
            threadState = 'resuming';
            renderThread();
            updateModeLabel();
        };
        // Reconnect, not a switch: the thread id carries over and user turns
        // paint; assistant bodies come back only through an authorized restore.
        messages = Persist.quarantineThread(saved.messages);
        if (!refs.length || !gate) { paint(); return; }
        window.McpAskAiRestoreApply = (data) => {
            messages = Persist.applyAuthorizedMessages(saved.messages, data);
            threadState = messages.length ? 'ready' : 'new';
            renderThread();
            updateModeLabel();
        };
        // A pending restore that is invalidated leaves notices, never blank shells.
        window.McpAskAiRestoreClear = () => {
            messages = Persist.settleQuarantined(messages);
            threadState = messages.length ? 'ready' : 'new';
            renderThread();
            updateModeLabel();
        };
        gate.invalidate();
        paint();
        gate.restore(Persist.restoreIdentity(saved.threadId), refs);
    }

    function nextId() {
        msgId += 1;
        return 'msg-' + msgId;
    }

    function csrfToken() {
        return document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
    }

    function core() {
        return window.McpAiCore;
    }

    function escapeHtml(text) {
        return core().escapeHtml(text);
    }

    function renderThinkingContent(msg) {
        return StreamUi.renderThinkingContent(msg, escapeHtml);
    }

    function updateModeLabel() {
        const label = root?.querySelector('.mcp-ask-ai__mode-label');
        const note = root?.querySelector('.mcp-ask-ai__meta-note');
        const newBtn = document.getElementById('mcpAskAiSidebar')?.querySelector('[data-mcp-ask-new]');
        if (threadState === 'independent') {
            if (label) label.textContent = 'Each question is answered independently';
            newBtn?.classList.add('mcp-ask-ai__new-chat--muted');
        } else if (threadState === 'error') {
            if (label) label.textContent = 'The last answer had a problem — try again';
            newBtn?.classList.remove('mcp-ask-ai__new-chat--muted');
        } else if (threadClearedNotice && threadState === 'new') {
            if (label) label.textContent = Persist.THREAD_CLEARED_NOTICE;
            newBtn?.classList.remove('mcp-ask-ai__new-chat--muted');
        } else {
            if (label) label.textContent = '';
            newBtn?.classList.remove('mcp-ask-ai__new-chat--muted');
        }
        if (note) note.hidden = !label?.textContent;
    }

    function showUnavailableState() {
        if (!threadEl) return;
        threadEl.innerHTML = StreamUi.unavailableStateHtml();
        if (textarea) textarea.disabled = true;
        if (sendBtn) sendBtn.disabled = true;
    }

    function setAvailability(available) {
        ragEnabled = !!available;
        if (root) {
            root.dataset.askAiAvailable = available ? '1' : '0';
            root.dataset.ragEnabled = available ? '1' : '0';
        }
        document.querySelectorAll('[data-mcp-ask-ai-toggle]').forEach((trigger) => {
            trigger.dataset.askAiAvailable = available ? '1' : '0';
            trigger.disabled = !available;
            trigger.setAttribute('aria-disabled', available ? 'false' : 'true');
        });
        document.querySelectorAll('[data-ask-ai-available]').forEach((element) => {
            element.dataset.askAiAvailable = available ? '1' : '0';
        });
        if (!available) {
            window.McpAskAiSidebar?.close?.({ restoreFocus: false });
            showUnavailableState();
        }
    }

    function scrollToBottom(force) {
        if (!threadEl) return;
        if (!force && userScrolledUp) return;
        threadEl.scrollTop = threadEl.scrollHeight;
    }

    let latePaintObserver = null;

    // Tables, cards and follow-ups paint after the thread render and on later
    // stream frames; scroll once per frame after any of them, unless the
    // person has scrolled up to read.
    function keepNewestInView() {
        latePaintObserver?.disconnect();
        latePaintObserver = null;
        if (!threadEl || typeof MutationObserver === 'undefined') return;
        let queued = false;
        latePaintObserver = new MutationObserver(() => {
            if (queued) return;
            queued = true;
            requestAnimationFrame(() => {
                queued = false;
                scrollToBottom(false);
            });
        });
        latePaintObserver.observe(threadEl, { childList: true, subtree: true, characterData: true });
    }

    // Only the person scrolls up to read: by wheel, touch, keys or the
    // scrollbar. Scroll events cannot tell, because the panel's own scrolls,
    // a re-render that briefly shortens the thread and scroll anchoring fire
    // them too.
    function listenForReaderScroll() {
        if (!threadEl) return;
        const reading = () => { userScrolledUp = true; };
        threadEl.addEventListener('wheel', (e) => { if (e.deltaY < 0) reading(); }, { passive: true });
        threadEl.addEventListener('touchmove', reading, { passive: true });
        threadEl.addEventListener('keydown', (e) => {
            if (e.key === 'PageUp' || e.key === 'ArrowUp' || e.key === 'Home') reading();
        });
        threadEl.addEventListener('pointerdown', (e) => {
            if (e.offsetX >= threadEl.clientWidth) reading();
        });
    }

    // Reaching the bottom again hands following back to the panel.
    function onThreadScroll() {
        if (!threadEl) return;
        if (threadEl.scrollHeight - threadEl.scrollTop - threadEl.clientHeight < 48) {
            userScrolledUp = false;
        }
        feedback?.position();
    }

    ui = StreamUi.createUi({
        escapeHtml,
        getThreadEl: () => threadEl,
        scrollToBottom,
        renderMarkdown: (text) => core().renderMarkdown(text),
        isSameOriginAppPath: (url) => core().isSameOriginAppPath(url),
    });

    const {
        updatePhaseTimeline,
        updateStreamingStatusUI,
        updateAssistantBubble,
        appendTurnPair,
        rebindStreamingBodyEls,
        renderAnswerContent,
        renderRecordLinks,
        renderMessagesHtml,
        bindThreadInteractions,
    } = ui;

    function findMessage(id) {
        return messages.find(m => m.id === id);
    }

    feedback = Feedback.createFeedbackController({
        findMessage,
        getFeedbackLayer: () => root?.querySelector('[data-mcp-ask-ai-feedback-layer]'),
        getFeedbackUrl: () => feedbackUrl,
        getRoot: () => root,
        getThread: () => threadEl,
        csrfToken,
        persistThread,
        escapeHtml,
    });

    function makeRevealer(assistant, onPaint, uiFirstTextRef) {
        let stamped = false;
        return StreamUi.makeRevealer(assistant, () => {
            onPaint();
            if (!stamped && String(assistant.content || '').trim()) {
                stamped = true;
                uiFirstTextRef?.stamp?.();
            }
        }, {
            revealDivisor: REVEAL_DIVISOR,
            maxLagChars: MAX_LAG_CHARS,
        });
    }

    function showEmptyState() {
        if (!threadEl || messages.length) return;
        threadEl.innerHTML = StreamUi.emptyStateHtml({
            starterPrompts,
            escapeHtml,
        });

        threadEl.querySelectorAll('.mcp-ask-ai__prompt').forEach(btn => {
            btn.addEventListener('click', () => {
                const idx = parseInt(btn.getAttribute('data-prompt-index'), 10);
                const prompt = starterPrompts[idx];
                if (prompt) open(prompt);
            });
        });
    }

    function announceCopied(messageId, btn) {
        const article = threadEl?.querySelector('[data-msg-id="' + messageId + '"]');
        let note = article?.querySelector('.mcp-ask-ai__copied') || null;
        if (!note && btn) {
            note = document.createElement('span');
            note.className = 'mcp-ask-ai__copied';
            note.setAttribute('role', 'status');
            btn.insertAdjacentElement('afterend', note);
        }
        if (note) note.textContent = 'Copied';
        btn?.setAttribute('data-mcp-tip', 'Copied');
        btn?.querySelector('i')?.classList.replace('fa-copy', 'fa-check');
        const sr = root?.querySelector('#mcp-ask-ai-sr');
        if (sr) sr.textContent = 'Copied';
        clearTimeout(copiedTimers.get(messageId));
        copiedTimers.set(messageId, setTimeout(() => {
            copiedTimers.delete(messageId);
            note?.remove();
            btn?.setAttribute('data-mcp-tip', 'Copy');
            btn?.querySelector('i')?.classList.replace('fa-check', 'fa-copy');
        }, COPIED_MS));
    }

    function renderThread() {
        if (!threadEl) return;

        if (!messages.length) {
            showEmptyState();
            return;
        }

        threadEl.innerHTML = renderMessagesHtml({
            messages,
            threadState,
        });

        bindThreadInteractions({
            onFeedback: feedback.submit,
            onTryAgain: tryAgain,
            onRegenerate: regenerate,
            onChip: (messageId, idx) => {
                const prompt = findMessage(messageId)?.followupSuggestions?.[idx];
                if (prompt) sendTurn(prompt);
            },
            onDisambiguate: (suggestedQuery) => {
                if (streamAbort) streamAbort.abort();
                sendTurn(suggestedQuery);
            },
            onCopy: (messageId, btn) => {
                if (window.McpAskAiRestoreGate?.state?.() === 'checking') return;
                const msg = findMessage(messageId);
                if (!msg) return;
                // A module that painted a typed result owns the readable text;
                // the message content is its hidden fallback.
                const provided = window.McpAskAiCopyText?.(messageId);
                const text = typeof provided === 'string' && provided.trim() ? provided : (msg.content || '');
                if (!text) return;
                copyTextToClipboard(text).then(() => announceCopied(messageId, btn)).catch(() => {});
            },
        });

        rebindStreamingBodyEls(messages);
        feedback.sync();
        scrollToBottom(false);
        // This rebuilds the thread's innerHTML, so anything layered onto a
        // message from outside this file is discarded. Announcing the render
        // lets those layers repaint from their own state.
        window.dispatchEvent(new CustomEvent('mcp:askai:thread-rendered'));
    }

    function setComposerMode(mode) {
        if (!sendBtn || !textarea) return;
        const streaming = mode === 'streaming';
        if (streaming) stopArmedAt = performance.now() + STOP_ARM_DELAY_MS;
        sendBtn.innerHTML = streaming
            ? '<i class="fa fa-stop" aria-hidden="true"></i><span class="visually-hidden">Stop</span>'
            : '<i class="fa fa-arrow-up" aria-hidden="true"></i><span class="visually-hidden">Send</span>';
        sendBtn.setAttribute('aria-label', streaming ? 'Stop generating' : 'Send question');
        sendBtn.classList.toggle('mcp-ask-ai__send--stop', streaming);
        textarea.disabled = streaming || !ragEnabled;
        threadEl?.setAttribute('aria-busy', streaming ? 'true' : 'false');
        updateSendState();
    }

    function updateSendState() {
        if (!sendBtn || !textarea) return;
        const trimmedLen = textarea.value.trim().length;
        const streaming = !!streamAbort;
        sendBtn.disabled = !ragEnabled || (!streaming && trimmedLen < MIN_QUERY);
    }

    function updateCounter() {
        if (!counterEl || !textarea) return;
        const len = textarea.value.length;
        counterEl.textContent = len + '/' + MAX_QUESTION;
        counterEl.hidden = len < MAX_QUESTION * 0.9; counterEl.classList.toggle('is-near', len >= MAX_QUESTION * 0.9);
        updateSendState();
        syncComposerHeight();
    }

    function syncComposerHeight() {
        if (!textarea) return;
        textarea.style.height = 'auto';
        const maxPx = parseFloat(window.getComputedStyle(textarea).maxHeight) || 144;
        textarea.style.height = Math.min(textarea.scrollHeight, maxPx) + 'px';
    }

    function newChat() {
        window.McpAskAiRestoreGate?.invalidate();
        claimTurn();
        if (streamAbort) {
            streamAbort.abort();
            streamAbort = null;
        }
        feedback.cancelActive();
        // Task B7 / R5: newChat is an explicit thread SWITCH, distinct from a
        // reconnect — clears the server thread_id so the next turn starts a
        // genuinely new thread rather than resuming the old one.
        currentThreadId = null;
        threadState = 'new';
        threadClearedNotice = false;
        window.McpAskAiComposer?.reset();
        messages = [];
        statusTimers.forEach((timer) => clearTimeout(timer));
        statusTimers.clear();
        elapsedTimers.forEach((handle) => clearInterval(handle));
        elapsedTimers.clear();
        copiedTimers.forEach(clearTimeout);
        copiedTimers.clear();
        userScrolledUp = false;
        srBatch?.stop();
        clearPersistedThread();
        setComposerMode('idle');
        updateModeLabel();
        renderThread();
        textarea?.focus();
    }

    /**
     * The empty fields a fresh assistant answer starts with. The new-turn
     * message and the regenerate reset both build from this list, so a field
     * added for one is never missing on the other.
     *
     * @param {object} retryDescriptor
     * @returns {object}
     */
    function emptyAnswerFields(retryDescriptor) {
        return {
            content: '',
            sources: [],
            citations: { parsed: false, cited: [] },
            recordLinks: [],
            uiLinks: [],
            sqlQueries: [],
            databaseEvidence: false,
            phases: [{ phase: 'understanding', label: 'Understanding your question' }],
            retryDescriptor,
            traceId: null,
            feedbackToken: null,
            followupSuggestions: [],
        };
    }

    function beginTurn(q, retryDescriptor) {
        window.McpAskAiRestoreGate?.invalidate();
        messages.push({ id: nextId(), role: 'user', content: q });
        const assistant = {
            id: nextId(),
            role: 'assistant',
            question: q,
            contextLabel: retryDescriptor.context_label,
            exchangeId: null,
            streaming: true,
            ...emptyAnswerFields(retryDescriptor),
        };
        messages.push(assistant);
        if (!appendTurnPair(messages[messages.length - 2], assistant)) {
            renderThread();
            assistant._bodyEl = threadEl?.querySelector(
                '[data-msg-id="' + assistant.id + '"] .mcp-ask-ai__msg-body'
            );
        }
        const turnId = claimTurn();
        streamAbort = new AbortController();
        setComposerMode('streaming');
        const srEl = root?.querySelector('#mcp-ask-ai-sr');
        const batch = core().scheduleSrBatch(srEl, () => assistant.content);
        batch.start();
        srBatch = batch;
        startNeutralStatusTimer(assistant, srEl);
        startElapsedTimer(assistant);
        return { assistant, srEl, turnId };
    }

    function rollbackTurn() {
        clearNeutralStatusTimer(messages.at(-1));
        messages.pop();
        messages.pop();
    }

    function shouldRetryAuth(err, options) {
        return !options.authRetried
            && err.name === 'PreStreamError'
            && (err.status === 401 || err.status === 419);
    }

    function handleStreamError(err, assistant, state) {
        clearNeutralStatusTimer(assistant);
        state?.revealer?.flush();
        if (err.name === 'AbortError') {
            assistant.streaming = false;
            assistant.stopped = true;
            // Stop is not a thread switch: the thread the request carried stays
            // the conversation's identity, so the next question resumes it.
            currentThreadId = currentThreadId || state?.sentBodyThreadId || currentThreadId;
            threadState = 'ready';
            window.dispatchEvent(new CustomEvent('mcp:askai:turn-stopped', {
                detail: { messageId: assistant.id, runId: activeRunId },
            }));
        } else if (err.name === 'PreStreamError') {
            assistant.streaming = false;
            assistant.error = err.message;
            if (err.status === 503) {
                window.dispatchEvent(new CustomEvent('mcp:askai:unavailable'));
            }
        } else if (err.name === 'CryptoUnavailableError') {
            assistant.streaming = false;
            assistant.error = CRYPTO_UNAVAILABLE_USER_MESSAGE;
        } else {
            assistant.streaming = false;
            assistant.error = 'Answer interrupted';
        }
        renderThread();
    }

    function finalizeTurn(assistant, state, turnId) {
        // The stream has closed (done, failed or stopped), so any charge for the
        // turn is landing now: read the month's figure.
        window.McpAskAiUsageMeter?.refresh({ afterTurn: state.completed === true });
        if (!isActiveTurn(turnId)) return;

        clearNeutralStatusTimer(assistant);
        state?.revealer?.flush();
        srBatch?.stop();
        srBatch?.flush();
        srBatch = null;
        streamAbort = null;
        activeRunId = null;
        setComposerMode('idle');

        if (!state.completed && !state.errored && !assistant.stopped) {
            state.errored = true;
            assistant.error = 'The answer did not finish. Try again.';
        }

        // Task B7 / R4: a genuine failure (not a user-initiated stop) surfaces
        // as the explicit 'error' state — a disconnect/interruption is NOT a
        // silent success, but it also does not clear currentThreadId (R5: a
        // disconnect is not a thread switch, the next turn can still resume it).
        if (state.errored) {
            threadState = 'error';
        }

        assistant.streaming = false;
        if (state.completed || state.errored || assistant.stopped || assistant.error) {
            renderThread();
        }

        if (textarea) {
            textarea.value = '';
            updateCounter();
        }

        persistThread();

        if (window.McpAskAiSidebar.isOpen()
            && (document.activeElement === document.body || document.activeElement === threadEl)) {
            textarea?.focus();
        }
    }

    async function runStreamTurn(descriptor, assistant, srEl, state, recordContext, turnId, uiFirstTextRef) {
        activeRunId = randomRunId();
        // Modules that paint into this turn start from this event, before any
        // server frame exists.
        window.dispatchEvent(new CustomEvent('mcp:askai:turn-started', {
            detail: { messageId: assistant.id, runId: activeRunId },
        }));
        // Task A9 (Bug 2): capture what we're ACTUALLY sending before the
        // request goes out — onDone compares the server's returned thread_id
        // against this, not the (possibly since-mutated) module-level variable.
        const sentThreadId = currentThreadId;
        const result = await StreamClient.sendQuestionAndParse(
            descriptor,
            assistant,
            srEl,
            state,
            recordContext,
            turnId,
            uiFirstTextRef,
            {
                core: core(),
                streamUrl,
                cancelUrl,
                timingUrl,
                getCurrentThreadId: () => sentThreadId,
                streamAbortSignal: streamAbort.signal,
                csrfToken: csrfToken(),
                runId: activeRunId,
                waitForLoaderPaint,
                PHASE_GROUPS,
                Persist,
                StreamUi,
                clearNeutralStatusTimer,
                updateStreamingStatusUI,
                isActiveTurn,
                setCurrentThreadId: (id) => { currentThreadId = id; },
                setThreadState: (nextState) => { threadState = nextState; },
                updateModeLabel,
                normalizeFollowupSuggestions,
                renderThread,
                persistThread,
                onBodyBuilt: ({ threadId }) => { state.sentBodyThreadId = threadId || null; },
            },
        );
        if (result?.terminal === 'done') {
            state.completed = true;
        }
        activeRunId = null;
    }

    async function sendTurn(question, options = {}) {
        if (!ragEnabled || !streamUrl) return;

        const q = (question || '').trim();
        if (q.length < MIN_QUERY || q.length > MAX_QUESTION) return;

        if (streamAbort) streamAbort.abort();

        const chosen = options.descriptor
            ? { pageContext: options.descriptor.page_context || null, recordContext: options.recordContext ?? options.descriptor.record_context ?? null }
            : (window.McpAskAiComposer?.contextForSend?.() || { pageContext: null, recordContext: null });
        const recordContext = options.recordContext !== undefined ? options.recordContext : chosen.recordContext;
        const pageContext = options.pageContext !== undefined ? options.pageContext : (recordContext ? null : chosen.pageContext);
        const descriptor = options.descriptor
            ? deepCloneFreeze(options.descriptor)
            : makeRetryDescriptor(q, pageContext, 'ask', null, recordContext);
        userScrolledUp = false;
        const { assistant, srEl, turnId } = beginTurn(q, descriptor);
        const uiFirstTextRef = { ms: null };
        const state = {
            errored: false,
            completed: false,
            revealer: makeRevealer(assistant, () => updateAssistantBubble(assistant), uiFirstTextRef),
        };

        let retry = false;
        try {
            await waitForLoaderPaint();
            if (!isActiveTurn(turnId)) return;
            await runStreamTurn(descriptor, assistant, srEl, state, recordContext, turnId, uiFirstTextRef);
        } catch (err) {
            if (shouldRetryAuth(err, options)) {
                currentThreadId = null;
                rollbackTurn();
                retry = true;
            } else {
                handleStreamError(err, assistant, state);
                if (err.name !== 'AbortError') {
                    state.errored = true;
                }
            }
        } finally {
            if (!retry) {
                finalizeTurn(assistant, state, turnId);
            } else if (isActiveTurn(turnId)) {
                srBatch?.stop();
                srBatch = null;
                streamAbort = null;
            }
        }

        if (retry) {
            return sendTurn(question, { authRetried: true, descriptor, recordContext });
        }
    }

    async function tryAgain(id) {
        const msg = findMessage(id);
        if (!msg?.retryDescriptor || streamAbort) return;
        const assistantIndex = messages.indexOf(msg);
        const user = messages[assistantIndex - 1];
        if (assistantIndex < 1 || user?.role !== 'user') return;
        const liveSnapshot = window.McpPageContext?.PageContextRegistry?.captureForSubmit?.() || null;
        // Task B7.1 / R2 fix: re-send the record refs that grounded the ORIGINAL
        // turn — the server re-resolves + re-authorizes them (B4 contract), so
        // retry no longer silently detaches record-context grounding.
        const recordContext = msg.retryDescriptor.record_context || null;
        const descriptor = makeRetryDescriptor(
            msg.retryDescriptor.question,
            liveSnapshot,
            'ask',
            null,
            recordContext
        );
        messages.splice(assistantIndex - 1, 2);
        renderThread();
        return sendTurn(descriptor.question, { descriptor, recordContext });
    }

    async function regenerate(id) {
        const assistant = findMessage(id);
        const latest = [...messages].reverse().find(
            (m) => m.role === 'assistant' && !m.streaming && !m.error && !m.stopped
        );
        if (!assistant || assistant !== latest || !assistant.exchangeId ||
            !assistant.retryDescriptor || streamAbort) return;

        const backup = {
            content: assistant.content,
            sources: assistant.sources,
            citations: assistant.citations,
            recordLinks: assistant.recordLinks,
            uiLinks: assistant.uiLinks,
            sqlQueries: assistant.sqlQueries,
            databaseEvidence: assistant.databaseEvidence,
            phases: assistant.phases,
            exchangeId: assistant.exchangeId,
            traceId: assistant.traceId,
            feedbackToken: assistant.feedbackToken,
            followupSuggestions: assistant.followupSuggestions,
            feedback: assistant.feedback,
            feedbackReason: assistant.feedbackReason,
            feedbackFormOpen: assistant.feedbackFormOpen,
            feedbackState: assistant.feedbackState,
        };
        // Task B7.1 / R2 fix: re-send the record refs that grounded the ORIGINAL
        // turn instead of hard-nulling them — the server re-resolves +
        // re-authorizes (B4 contract) and rebuilds the reference artifact,
        // repairing the thread's reference ledger instead of destroying it.
        const recordContext = assistant.retryDescriptor.record_context || null;
        const descriptor = makeRetryDescriptor(
            assistant.retryDescriptor.question,
            assistant.retryDescriptor.page_context,
            'regenerate',
            assistant.exchangeId,
            recordContext
        );
        Object.assign(assistant, emptyAnswerFields(descriptor), {
            streaming: true,
            stopped: false,
            error: null,
            feedback: null,
            feedbackReason: null,
            feedbackFormOpen: false,
            feedbackState: null,
        });
        const turnId = claimTurn();
        streamAbort = new AbortController();
        setComposerMode('streaming');
        renderThread();
        assistant._bodyEl = threadEl?.querySelector(
            '[data-msg-id="' + assistant.id + '"] .mcp-ask-ai__msg-body'
        );
        const srEl = root?.querySelector('#mcp-ask-ai-sr');
        srBatch?.stop();
        const batch = core().scheduleSrBatch(srEl, () => assistant.content);
        batch.start();
        srBatch = batch;
        startNeutralStatusTimer(assistant, srEl);
        startElapsedTimer(assistant);
        const uiFirstTextRef = { ms: null };
        const state = {
            errored: false,
            completed: false,
            revealer: makeRevealer(assistant, () => updateAssistantBubble(assistant), uiFirstTextRef),
        };
        try {
            await waitForLoaderPaint();
            await runStreamTurn(descriptor, assistant, srEl, state, recordContext, turnId, uiFirstTextRef);
        } catch (error) {
            state.errored = true;
            handleStreamError(error, assistant, state);
        } finally {
            if (!isActiveTurn(turnId)) return;

            clearNeutralStatusTimer(assistant);
            state.revealer.flush();
            srBatch?.stop();
            srBatch?.flush();
            srBatch = null;
            if (!state.completed || state.errored || assistant.stopped || assistant.error) {
                Object.assign(assistant, backup, {
                    streaming: false,
                    retryDescriptor: makeRetryDescriptor(
                        descriptor.question,
                        descriptor.page_context,
                        'ask',
                        null,
                        descriptor.record_context
                    ),
                    error: null,
                    stopped: false,
                });
            } else {
                assistant.streaming = false;
            }
            streamAbort = null;
            activeRunId = null;
            setComposerMode('idle');
            renderThread();
            persistThread();
        }
    }

    function handleSendAction() {
        if (streamAbort) {
            streamAbort.abort();
        } else {
            sendTurn(textarea?.value || '');
        }
    }

    function open(seedQuestion, recordContext, recordContextLabel) {
        window.McpAskAiSidebar.open(null, { focus: false });
        if (recordContext) {
            window.McpAskAiComposer?.stageRecord(recordContext, recordContextLabel);
            if (textarea) {
                textarea.value = seedQuestion || '';
                updateCounter();
            }
            textarea?.focus();
            return;
        }
        if (seedQuestion && String(seedQuestion).trim().length >= MIN_QUERY) {
            sendTurn(seedQuestion, {});
        } else if (!messages.length && ragEnabled) {
            showEmptyState();
        }
        textarea?.focus();
    }

    // Task P7 — delegated trigger for record-only "Ask about this <record>"
    // buttons rendered OUTSIDE the Ask AI panel itself (currently bill.show's
    // "Ask about this bill", both desktop and mobile views). A document-level
    // listener is required (not a direct bind at boot()) because these
    // buttons live inside Livewire-morphed page content, which is replaced on
    // every Livewire re-render — a one-time direct bind would go stale.
    const RECORD_TRIGGER_SEL = '[data-mcp-ask-ai-record-trigger]';

    /**
     * Untrusted record_context — identifiers only, mirroring
     * mcp-global-search.js's buildRecordContext(). Billing re-resolves and
     * re-authorizes the reference server-side (RecordContextResolver) before
     * anything reaches RAG; the trigger's data attributes confer no
     * authority. `query` is derived from the identifiers themselves (never
     * from the display label — the label is a display-only companion to the
     * chip, same separation Global Search's recordContextLabel() keeps).
     */
    function buildRecordContextFromTrigger(trigger) {
        const resourceType = trigger?.dataset?.recordResourceType;
        const recordId = trigger?.dataset?.recordId;
        if (!resourceType || !recordId) return null;
        return {
            version: 1,
            origin: 'explicit_selection',
            query: resourceType + ' #' + recordId,
            references: [{ resource_type: resourceType, record_id: String(recordId) }],
        };
    }

    function onDocumentClickForRecordTrigger(e) {
        const trigger = e.target.closest(RECORD_TRIGGER_SEL);
        if (!trigger) return;
        e.preventDefault();
        if (root?.dataset.askAiAvailable !== '1') return;
        const recordContext = buildRecordContextFromTrigger(trigger);
        if (!recordContext) return;
        // Stages a removable chip via the existing open() flow — never
        // auto-sends (Task B7 / R1's staging contract, reused verbatim).
        open('', recordContext, trigger.dataset.recordLabel || null);
    }

    function onComposerKeydown(e) {
        if (e.isComposing || e.keyCode === 229) return;

        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSendAction();
        }
    }

    // The second click of a double click, or any click before Stop is armed,
    // belongs to the Send that started this turn and never stops it.
    function onSendClick(event) {
        if (streamAbort && (event?.detail > 1 || performance.now() < stopArmedAt)) return;
        handleSendAction();
    }

    function stopStream() {
        if (streamAbort) streamAbort.abort();
    }

    function boot() {
        root = document.getElementById('mcp-ask-ai-root');
        if (!root) return;

        streamUrl = root.dataset.streamUrl || '';
        feedbackUrl = root.dataset.feedbackUrl || '';
        timingUrl = root.dataset.timingUrl || '';
        cancelUrl = root.dataset.cancelUrl || '';
        ragEnabled = (root.dataset.askAiAvailable || root.dataset.ragEnabled) === '1';
        try {
            starterPrompts = JSON.parse(root.dataset.starters || '[]');
        } catch (_) {
            starterPrompts = [];
        }

        threadEl = root.querySelector('.mcp-ask-ai__thread');
        textarea = root.querySelector('.mcp-ask-ai__input');
        counterEl = root.querySelector('.mcp-ask-ai__counter');
        sendBtn = root.querySelector('.mcp-ask-ai__send');

        window.McpAskAiComposer?.init(root, { contextSubject: StreamUi.contextSubject });
        window.McpAskAiUsageMeter?.init(root);

        window.McpAskAiSidebar.setHooks({
            onOpen() {
                ragEnabled = (root.dataset.askAiAvailable || root.dataset.ragEnabled) === '1';
                if (!messages.length) {
                    if (ragEnabled) showEmptyState();
                    else showUnavailableState();
                }
                updateCounter();
            },
            onClosing() {
                window.McpAskAiRestoreGate?.invalidate();
                stopStream();
                feedback.cancelActive();
            },
            onClose() {
                // Stream already aborted in onClosing; kept for API symmetry.
            },
        });

        restoreThread();
        window.McpAskAiSidebar.restorePersistedState();

        document.getElementById('topbarLogoutForm')?.addEventListener('submit', () => {
            window.McpAskAiRestoreGate?.invalidate();
            clearPersistedThread();
        });

        const shell = document.getElementById('mcpAskAiSidebar');
        shell?.querySelector('[data-mcp-ask-new]')?.addEventListener('click', newChat);
        threadEl?.addEventListener('scroll', onThreadScroll);
        keepNewestInView();
        listenForReaderScroll();
        root.querySelector('[data-mcp-ask-ai-feedback-layer]')
            ?.addEventListener('keydown', feedback.handleKeydown);
        textarea?.addEventListener('input', updateCounter);
        textarea?.addEventListener('keydown', onComposerKeydown);
        sendBtn?.addEventListener('click', onSendClick);
        window.addEventListener('resize', feedback.position);
        window.addEventListener('mcp:askai:unavailable', () => setAvailability(false));
        // A layer outside this file asks the panel to send on its behalf. The
        // panel keeps one send site; the layer supplies the words and, when the
        // turn is not a plain question, the descriptor that names it.
        window.addEventListener('mcp:askai:send', (event) => {
            const { label, descriptor } = event.detail || {};
            if (typeof label !== 'string' || !label.trim()) return;
            sendTurn(label, descriptor ? { descriptor } : {});
        });
        document.addEventListener('click', onDocumentClickForRecordTrigger);

        updateCounter();
        if (ragEnabled) {
            showEmptyState();
        } else {
            showUnavailableState();
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }

    window.AskAi = {
        open,
        newChat,
        sendTurn,
        tryAgain,
        regenerate,
        stopStream,
        get currentThreadId() { return currentThreadId; },
        get messages() { return messages.slice(); },
        get threadState() { return threadState; },
        /** @internal test helpers */
        _test: {
            makeRevealer,
            renderThinkingContent,
            loaderStageForMsg,
            loaderSrcForMsg,
            displayLabelForMsg,
            updateStreamingStatusUI,
            updatePhaseTimeline,
            updateAssistantBubble,
            renderAnswerContent,
            renderRecordLinks,
            patchFeedbackButtons: feedback.patchFeedbackButtons,
            renderThread,
            findMessage,
            deepCloneFreeze,
            makeRetryDescriptor,
            contextLabel,
            startNeutralStatusTimer,
            clearNeutralStatusTimer,
            normalizeFollowupSuggestions,
            waitForLoaderPaint,
            NEUTRAL_STATUS_DELAY_MS,
            getMessages() { return messages; },
            setMessages(next) { messages = next; },
            setThreadState(state) { threadState = state; },
            setThreadEl(el) { threadEl = el; },
            setRoot(el) { root = el; },
            keepNewestInView,
            onThreadScroll,
            randomRunId,
        },
    };

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = window.AskAi;
    }
})();
