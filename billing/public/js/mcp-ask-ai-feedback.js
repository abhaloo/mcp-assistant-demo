/*
 * MCP Ask AI — feedback state, popover HTML/interactions, and submission.
 */
(function () {
    'use strict';

    const FEEDBACK_REASONS = [
        ['incorrect', 'Incorrect'],
        ['incomplete', 'Incomplete'],
        ['irrelevant', 'Not relevant'],
        ['unsafe', 'Unsafe'],
        ['other', 'Other'],
    ];

    function safeDomId(value) {
        return String(value || 'feedback').replace(/[^A-Za-z0-9_-]/g, '-');
    }

    function renderFeedbackPopoverHtml(msg, escapeHtml) {
        if (!msg || msg.feedback !== 'down' || msg.feedbackFormOpen === false) return '';
        const esc = escapeHtml || ((t) => String(t));
        const titleId = 'feedback-title-' + safeDomId(msg.id);
        const descriptionId = 'feedback-description-' + safeDomId(msg.id);
        const commentId = 'feedback-comment-' + safeDomId(msg.id);
        const isSending = msg.feedbackState === 'sending';
        return [
            '<section class="mcp-ask-ai__feedback-card" role="dialog" aria-modal="false" aria-labelledby="' +
                titleId + '" aria-describedby="' + descriptionId + '" data-feedback-popover="' +
                esc(msg.id) + '">',
            '  <button type="button" class="mcp-ask-ai__feedback-close" data-feedback-cancel="' +
                esc(msg.id) + '" aria-label="Close feedback form"><i class="fa fa-xmark" aria-hidden="true"></i></button>',
            '  <div class="mcp-ask-ai__feedback-heading">',
            '    <span class="mcp-ask-ai__feedback-icon" aria-hidden="true"><i class="fa fa-message"></i></span>',
            '    <span><strong id="' + titleId + '">What could be better?</strong>',
            '      <span id="' + descriptionId + '">Select an issue and add any useful detail.</span></span>',
            '  </div>',
            '  <fieldset class="mcp-ask-ai__feedback-reasons"><legend class="visually-hidden">Feedback reason</legend>',
            FEEDBACK_REASONS.map(([value, label]) =>
                '<label class="mcp-ask-ai__feedback-option"><input type="radio" name="feedback-' +
                esc(msg.id) + '" value="' + value + '" data-feedback-reason="' +
                esc(msg.id) + '"' + (msg.feedbackReason === value ? ' checked' : '') +
                (isSending ? ' disabled' : '') +
                '><span>' + label + '</span></label>'
            ).join(''),
            '  </fieldset>',
            '  <label class="mcp-ask-ai__feedback-comment-label" for="' + commentId + '">Tell us more <span>(optional)</span></label>',
            '  <textarea id="' + commentId + '" class="mcp-ask-ai__feedback-comment" rows="3" maxlength="500"',
            '    data-feedback-comment="' + esc(msg.id) + '" placeholder="What did you expect instead?"' +
                (isSending ? ' disabled' : '') + '>' + esc(msg.feedbackComment || '') + '</textarea>',
            msg.feedbackState === 'error'
                ? '  <p class="mcp-ask-ai__feedback-error">Could not send feedback. Please try again.</p>'
                : '',
            '  <div class="mcp-ask-ai__feedback-actions">',
            '    <button type="button" class="mcp-btn mcp-btn--ghost mcp-btn--sm" data-feedback-cancel="' +
                esc(msg.id) + '"' + (isSending ? ' disabled' : '') + '>Cancel</button>',
            '    <button type="button" class="mcp-btn mcp-btn--primary mcp-btn--sm" data-feedback-submit="' +
                esc(msg.id) + '"' + (msg.feedbackReason && !isSending ? '' : ' disabled') + '>' +
                (isSending ? 'Sending…' : 'Send feedback') + '</button>',
            '  </div>',
            '</section>',
        ].join('');
    }

    function bindFeedbackPopoverInteractions(container, handlers) {
        container?.querySelectorAll('[data-feedback-reason]').forEach((input) => {
            input.addEventListener('change', () => handlers.onFeedbackReason?.(
                input.getAttribute('data-feedback-reason'), input.value
            ));
        });
        container?.querySelectorAll('[data-feedback-comment]').forEach((input) => {
            input.addEventListener('input', () => handlers.onFeedbackComment?.(
                input.getAttribute('data-feedback-comment'), input.value
            ));
        });
        container?.querySelectorAll('[data-feedback-submit]').forEach((btn) => {
            btn.addEventListener('click', () => handlers.onFeedbackSubmit?.(
                btn.getAttribute('data-feedback-submit')
            ));
        });
        container?.querySelectorAll('[data-feedback-cancel]').forEach((btn) => {
            btn.addEventListener('click', () => handlers.onFeedbackCancel?.(
                btn.getAttribute('data-feedback-cancel')
            ));
        });
    }

    function patchFeedbackButtons(msg, getThread) {
        const article = getThread()?.querySelector('[data-msg-id="' + msg.id + '"]');
        article?.querySelectorAll('[data-feedback]').forEach((btn) => {
            const on = msg.feedback === btn.getAttribute('data-feedback');
            btn.classList.toggle('is-selected', on);
            btn.setAttribute('aria-pressed', on ? 'true' : 'false');
            if (on) {
                btn.classList.remove('mcp-ask-ai__thumb--pop');
                void btn.offsetWidth;
                btn.classList.add('mcp-ask-ai__thumb--pop');
            }
        });
    }

    function createFeedbackController(deps) {
        const {
            findMessage,
            getFeedbackLayer,
            getFeedbackUrl,
            getRoot,
            getThread,
            csrfToken,
            persistThread,
            escapeHtml,
        } = deps;

        let activeMessageId = null;

        function snapshot(msg) {
            return {
                feedback: msg.feedback || null,
                feedbackReason: msg.feedbackReason || null,
                feedbackComment: msg.feedbackComment || '',
                feedbackState: msg.feedbackState || null,
            };
        }

        function restoreSnapshot(msg, previous) {
            Object.assign(msg, previous || {
                feedback: null,
                feedbackReason: null,
                feedbackComment: '',
                feedbackState: null,
            });
        }

        function patchButtons(msg) {
            patchFeedbackButtons(msg, getThread);
        }

        function hide({ restoreFocus = false } = {}) {
            const messageId = activeMessageId;
            activeMessageId = null;
            const layer = getFeedbackLayer();
            if (layer) {
                layer.hidden = true;
                layer.innerHTML = '';
            }
            if (restoreFocus && messageId) {
                getThread()?.querySelector(
                    '[data-feedback="down"][data-msg-id="' + messageId + '"]'
                )?.focus();
            }
        }

        function position() {
            const layer = getFeedbackLayer();
            if (!layer || layer.hidden || !activeMessageId) return;
            const card = layer.querySelector('.mcp-ask-ai__feedback-card');
            const anchor = getThread()?.querySelector(
                '[data-feedback="down"][data-msg-id="' + activeMessageId + '"]'
            );
            const composer = getRoot()?.querySelector('.mcp-ask-ai__composer');
            if (!card || !anchor || !composer) return;

            const layerRect = layer.getBoundingClientRect();
            const anchorRect = anchor.getBoundingClientRect();
            const composerRect = composer.getBoundingClientRect();
            if (!layerRect.width || !anchorRect.width) return;

            const edge = 16;
            const gap = 8;
            const width = Math.min(360, layerRect.width - (edge * 2));
            card.style.width = width + 'px';
            const maxLeft = Math.max(edge, layerRect.width - width - edge);
            card.style.left = Math.min(
                Math.max(anchorRect.right - layerRect.left - width, edge),
                maxLeft
            ) + 'px';
            card.style.right = 'auto';

            const below = anchorRect.bottom - layerRect.top + gap;
            const availableBottom = composerRect.top - layerRect.top - gap;
            const above = anchorRect.top - layerRect.top - card.offsetHeight - gap;
            card.style.top = (
                below + card.offsetHeight <= availableBottom ? below : Math.max(edge, above)
            ) + 'px';
            card.style.bottom = 'auto';
        }

        function sync({ focus = false } = {}) {
            const layer = getFeedbackLayer();
            if (!layer) return;
            const msg = activeMessageId ? findMessage(activeMessageId) : null;
            const html = renderFeedbackPopoverHtml(msg, escapeHtml);
            if (!html) {
                hide();
                return;
            }

            layer.innerHTML = html;
            layer.hidden = false;
            bindFeedbackPopoverInteractions(layer, {
                onFeedbackReason: selectReason,
                onFeedbackComment: updateComment,
                onFeedbackSubmit: submitSelected,
                onFeedbackCancel: cancel,
            });
            position();
            if (focus) {
                const selected = layer.querySelector('[data-feedback-reason]:checked');
                (selected || layer.querySelector('[data-feedback-reason]'))?.focus();
            }
        }

        function selectReason(id, reason) {
            const msg = findMessage(id);
            const allowed = FEEDBACK_REASONS.some(([value]) => value === reason);
            if (!msg || !allowed) return;
            msg.feedbackReason = reason;
            msg.feedbackState = 'draft';
            const submit = getFeedbackLayer()?.querySelector(
                '[data-feedback-submit="' + id + '"]'
            );
            if (submit) submit.disabled = false;
        }

        function updateComment(id, comment) {
            const msg = findMessage(id);
            if (!msg) return;
            msg.feedbackComment = String(comment || '').slice(0, 500);
            if (msg.feedbackState === 'error') {
                msg.feedbackState = 'draft';
                getFeedbackLayer()?.querySelector('.mcp-ask-ai__feedback-error')?.remove();
            }
        }

        function submitSelected(id) {
            const msg = findMessage(id);
            if (!msg?.feedbackReason) return;
            return submit(id, 'down', msg.feedbackReason, msg.feedbackComment || '');
        }

        function cancel(id = activeMessageId) {
            const msg = id ? findMessage(id) : null;
            if (!msg || msg.feedbackState === 'sent') return;
            restoreSnapshot(msg, msg.feedbackBeforeDraft);
            msg.feedbackBeforeDraft = null;
            msg.feedbackFormOpen = false;
            hide({ restoreFocus: true });
            patchButtons(msg);
        }

        async function submit(id, direction, reason = null, comment = '') {
            const msg = findMessage(id);
            if (!msg?.traceId || !msg.feedbackToken) return;
            if (direction === 'down' && !reason) {
                if (activeMessageId && activeMessageId !== id) cancel(activeMessageId);
                const wasDown = msg.feedback === 'down';
                msg.feedbackBeforeDraft = snapshot(msg);
                msg.feedback = 'down';
                msg.feedbackReason = wasDown ? msg.feedbackReason : null;
                msg.feedbackComment = wasDown ? (msg.feedbackComment || '') : '';
                msg.feedbackFormOpen = true;
                msg.feedbackState = 'draft';
                activeMessageId = id;
                patchButtons(msg);
                sync({ focus: true });
                return;
            }

            const previous = msg.feedbackBeforeDraft || snapshot(msg);
            const normalizedComment = String(comment || '').trim().slice(0, 500);
            Object.assign(msg, {
                feedback: direction,
                feedbackReason: reason,
                feedbackComment: normalizedComment,
                feedbackFormOpen: direction === 'down',
                feedbackState: 'sending',
            });
            patchButtons(msg);
            if (activeMessageId === id) sync();

            const srElement = getRoot()?.querySelector('#mcp-ask-ai-sr');
            try {
                const response = await fetch(getFeedbackUrl(), {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-TOKEN': csrfToken(),
                        'X-Requested-With': 'XMLHttpRequest',
                    },
                    body: JSON.stringify({
                        trace_id: msg.traceId,
                        feedback_token: msg.feedbackToken,
                        verdict: direction,
                        ...(reason ? { reason } : {}),
                        ...(normalizedComment ? { comment: normalizedComment } : {}),
                    }),
                    credentials: 'same-origin',
                });
                if (!response.ok) throw new Error('feedback failed');

                if (srElement) srElement.textContent = 'Thanks for your feedback';
                msg.feedbackState = 'sent';
                msg.feedbackFormOpen = false;
                msg.feedbackBeforeDraft = null;
                if (activeMessageId === id) hide();
                patchButtons(msg);
                persistThread();
            } catch (_) {
                if (direction === 'down') {
                    Object.assign(msg, {
                        feedback: 'down',
                        feedbackReason: reason,
                        feedbackComment: normalizedComment,
                        feedbackState: 'error',
                        feedbackFormOpen: true,
                    });
                    activeMessageId = id;
                    sync();
                } else {
                    restoreSnapshot(msg, previous);
                    msg.feedbackBeforeDraft = null;
                    msg.feedbackFormOpen = false;
                    if (activeMessageId === id) hide();
                    patchButtons(msg);
                }
                if (srElement) srElement.textContent = 'Could not send feedback — try again.';
            }
        }

        function handleKeydown(event) {
            if (event.key !== 'Escape' || !activeMessageId) return;
            event.preventDefault();
            cancel(activeMessageId);
        }

        return {
            cancelActive: () => cancel(activeMessageId),
            handleKeydown,
            patchFeedbackButtons: patchButtons,
            position,
            submit,
            sync,
        };
    }

    window.McpAskAiFeedback = {
        FEEDBACK_REASONS,
        createFeedbackController,
        renderFeedbackPopoverHtml,
        bindFeedbackPopoverInteractions,
        patchFeedbackButtons,
    };
})();
