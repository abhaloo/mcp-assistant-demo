/*
 * MCP Ask AI — streaming UI helpers (loaders, phase timeline, turn DOM).
 */
(function () {
    'use strict';

    /**
     * Marks an anchor as leaving the panel: the answer and its thread stay put
     * while the record opens beside them. `label` names the destination for a
     * reader who cannot see the link's surroundings.
     */
    function openInNewTab(anchor, label) {
        anchor.setAttribute('target', '_blank');
        anchor.setAttribute('rel', 'noopener');
        anchor.setAttribute('aria-label', (label ? label + ' ' : '') + '(opens in a new tab)');
        return anchor;
    }

    const PHASE_GROUPS = {
        understanding: 'Understanding',
        classifying: 'Understanding',
        working: 'Working on your request',
        searching_documents: 'Finding information',
        searching_database: 'Finding information',
        searching_both: 'Finding information',
        inspecting_access: 'Checking access',
        writing_query: 'Checking access',
        running_query: 'Checking access',
        reranking: 'Finding information',
        writing_answer: 'Writing answer',
    };
    const CONTEXT_LABELS = {
        'work-order.index|list': 'Jobs on this page',
        'work-order.show|detail': 'Job on this page',
        'report.work-order|report': 'Jobs report',
        'customer.show|embedded_list': 'Jobs for customer',
        'bill.show|embedded_list': 'Jobs on bill',
        'customer-orders.show|embedded_list': 'Jobs on customer order',
    };
    const CONTEXT_SUBJECTS = {
        'work-order.index|list': 'Jobs',
        'work-order.show|detail': 'Job',
        'report.work-order|report': 'Jobs report',
        'customer.show|embedded_list': 'Customer jobs',
        'bill.show|embedded_list': 'Bill jobs',
        'customer-orders.show|embedded_list': 'Customer order jobs',
    };
    // Display names for a record ref's `table` key. Mirrors the canonical
    // Billing resource labels (DefaultResources) that the API is not yet
    // exposing to the browser. RAG sends links from two paths: `ref.resource`
    // (invoice, job, …) and `record.link_key` (the route keys, bills, …), so
    // both spellings map here. An unknown key falls back to a readable name.
    const RECORD_TYPE_LABELS = {
        invoice: 'Invoice',
        bills: 'Invoice',
        quotation: 'Quotation',
        payable_quotation: 'Payable quotation',
        customer_order: 'Customer order',
        customer_orders: 'Customer order',
        job: 'Job',
        work_orders: 'Job',
        customer: 'Customer',
        customers: 'Customer',
        supplier: 'Supplier',
        inventory: 'Inventory',
    };

    function recordTypeLabel(table) {
        const key = typeof table === 'string' ? table.trim() : '';
        if (!key) return '';
        if (Object.prototype.hasOwnProperty.call(RECORD_TYPE_LABELS, key)) {
            return RECORD_TYPE_LABELS[key];
        }
        const spaced = key.replace(/_/g, ' ');
        return spaced.charAt(0).toUpperCase() + spaced.slice(1);
    }

    const FULL_LOADER_SRC = '/img/loaders/ai-chat-loader.svg';

    function prefersReducedMotion() {
        return typeof window.matchMedia === 'function'
            && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    }

    // A finished answer can carry parts the panel switch turns off. The root
    // holds each switch as a data attribute and is read at every paint, so a
    // page can flip it live. A missing root or attribute keeps the part on.
    function answerPartEnabled(datasetKey) {
        const root = typeof document !== 'undefined'
            ? document.getElementById('mcp-ask-ai-root')
            : null;
        if (!root) return true;
        const value = root.dataset[datasetKey];
        return value === undefined ? true : value !== '0';
    }

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

    function restoreCopyBlocked() {
        return window.McpAskAiRestoreGate?.state?.() === 'checking';
    }

    function deepCloneFreeze(value) {
        if (value === null || typeof value !== 'object') return value;
        const clone = Array.isArray(value) ? value.map(deepCloneFreeze) : {};
        if (!Array.isArray(value)) {
            Object.keys(value).forEach((key) => {
                clone[key] = deepCloneFreeze(value[key]);
            });
        }
        return Object.freeze(clone);
    }

    /**
     * Canonical Business Query result envelope from the RAG terminal frame.
     * Keep this tolerant for progressive rollout: old frames return null and
     * continue through the legacy presentation path.
     */
    function businessQueryEnvelope(raw) {
        if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
        if (raw.outcome === 'answered') {
            return raw.envelope && typeof raw.envelope === 'object' && !Array.isArray(raw.envelope)
                ? deepCloneFreeze(raw.envelope)
                : null;
        }
        // Refusal/clarification outcomes have no result envelope. Keep their
        // typed wrapper available to the caller; never reinterpret them as an
        // answered result or manufacture counts/actions.
        return deepCloneFreeze(raw);
    }

    function isCustomerOrderEnvelope(envelope) {
        if (!envelope || typeof envelope !== 'object') return false;
        const rows = Array.isArray(envelope.rows) ? envelope.rows : [];
        const records = Array.isArray(envelope.records)
            ? envelope.records.map((record) => record?.fields)
            : [];
        const aggregates = Array.isArray(envelope.aggregates) ? envelope.aggregates : [];
        return rows.concat(records, aggregates.map((aggregate) => aggregate?.fields)).some((row) => (
            row && typeof row === 'object' && Object.keys(row).some((key) => (
                key.startsWith('customer_order.')
            ))
        ));
    }

    function safeMetadataText(value, maxLength = 256) {
        if (typeof value === 'string') {
            const text = value.trim();
            return text ? text.slice(0, maxLength) : null;
        }
        if (typeof value === 'number' && Number.isFinite(value)) return String(value);
        if (typeof value === 'boolean') return String(value);
        return null;
    }

    function detailDisplayValue(detail) {
        const display = safeMetadataText(detail?.display_value);
        if (display !== null) return display;
        if (detail?.typed_value !== null && typeof detail?.typed_value === 'object') {
            try { return JSON.stringify(detail.typed_value); } catch (_) { return ''; }
        }
        return safeMetadataText(detail?.typed_value) || '';
    }

    function detailProvenanceText(provenance) {
        const direct = safeMetadataText(provenance);
        if (direct !== null) return direct;
        if (!provenance || typeof provenance !== 'object' || Array.isArray(provenance)) return null;

        // Provenance is evidence metadata, not display content. Only expose
        // known scalar fields; never stringify arbitrary producer-controlled
        // objects into the trust drawer.
        const fields = ['projection_view', 'source', 'provenance', 'source_fingerprint', 'source_updated_at'];
        const parts = fields.map((name) => {
            const rawVal = provenance[name];
            if (rawVal === null || rawVal === undefined) return null;
            if (name === 'provenance' && typeof rawVal === 'string' && rawVal.startsWith('{') && rawVal.endsWith('}')) {
                try {
                    const parsed = JSON.parse(rawVal.replace(/'/g, '"'));
                    const formatted = Object.entries(parsed).map(([k, v]) => k + ': ' + v).join(', ');
                    return 'source_location: (' + formatted + ')';
                } catch (_) {
                    return null;
                }
            }
            const value = safeMetadataText(rawVal);
            return value === null ? null : name + ': ' + value;
        }).filter(Boolean);
        return parts.length ? parts.join('; ') : null;
    }

    function recordOwnerLabel(ref) {
        if (!ref || typeof ref !== 'object' || Array.isArray(ref)) return null;
        const resource = safeMetadataText(ref.resource, 64);
        const recordId = safeMetadataText(ref.record_id, 128);
        const label = safeMetadataText(ref.label, 128);
        const identity = resource && recordId ? resource + ' #' + recordId : resource || recordId;
        if (label && identity) return label + ' (' + identity + ')';
        return label || identity || null;
    }

    function detailMetadata(detail) {
        const provenance = detail?.provenance;
        const unit = safeMetadataText(detail?.unit)
            || (provenance && typeof provenance === 'object' ? safeMetadataText(provenance.unit) : null);
        const revision = safeMetadataText(detail?.revision_hash);
        const defRevision = safeMetadataText(detail?.definition_revision);

        const rows = [];
        if (revision && defRevision && revision === defRevision) {
            rows.push(['Definition revision', defRevision]);
        } else {
            if (revision) rows.push(['Revision', revision]);
            if (defRevision) rows.push(['Definition revision', defRevision]);
        }
        rows.push(
            ['Profile revision', safeMetadataText(detail?.profile_revision_hash || detail?.profile_revision)],
            ['Observation', safeMetadataText(detail?.observation_version)],
            ['Unit', unit],
            ['Provenance', detailProvenanceText(provenance)]
        );
        return rows.filter(([, value]) => value !== null);
    }

    /**
     * Return detail evidence in the canonical record-owned shape. The flat
     * envelope.record_details field is only read when an old producer did not
     * emit envelope.records at all; canonical records are authoritative even
     * when they contain no details.
     */
    function recordDetailEvidence(envelope) {
        if (!envelope || typeof envelope !== 'object') return [];
        if (Array.isArray(envelope.records)) {
            return envelope.records.flatMap((record) => {
                const owner = recordOwnerLabel(record?.ref);
                if (!owner || !Array.isArray(record?.details)) return [];
                return record.details
                    .filter((detail) => detail && typeof detail.family === 'string' && detail.family.trim())
                    .map((detail) => ({
                        owner,
                        family: detail.family.trim(),
                        value: detailDisplayValue(detail),
                        metadata: detailMetadata(detail),
                    }));
            });
        }

        return Array.isArray(envelope.record_details)
            ? envelope.record_details
                .filter((detail) => detail && typeof detail.family === 'string' && detail.family.trim())
                .map((detail) => ({
                    owner: null,
                    family: detail.family.trim(),
                    value: detailDisplayValue(detail),
                    metadata: detailMetadata(detail),
                }))
            : [];
    }

    function displayAnswerContent(msg) {
        const content = String(msg?.content ?? '');
        const wire = msg?.businessQuery || msg?.business_query;
        const envelope = wire?.outcome === 'answered' ? businessQueryEnvelope(wire) : null;
        if (!isCustomerOrderEnvelope(envelope)) return content;

        // The producer currently serializes SQL NULL as the Python sentinel
        // "None" in answer_text. Keep the correction scoped to customer-order
        // rows so a literal value in another domain is never rewritten.
        return content.replace(/\bNone\b/g, 'Not provided');
    }

    function trustPlainEnglish(exp, envelope) {
        const plainEnglish = typeof exp?.plain_english === 'string'
            ? exp.plain_english
            : '';
        if (isCustomerOrderEnvelope(envelope)
            && /^Showing customers matching your specified criteria\.$/i.test(plainEnglish.trim())) {
            return 'Showing customer orders matching your specified criteria.';
        }
        return plainEnglish;
    }

    const ANSWER_QUERY_ID_PATTERN = /^[A-Za-z0-9._:-]{1,128}$/;

    function answerQueryIdForEnvelope(envelope) {
        if (!envelope || typeof envelope !== 'object' || Array.isArray(envelope)) return null;

        const hasEnvelopeId = Object.prototype.hasOwnProperty.call(envelope, 'answer_query_id');
        const receipt = envelope.receipt;
        const hasReceipt = Object.prototype.hasOwnProperty.call(envelope, 'receipt');
        if (hasReceipt && (!receipt || typeof receipt !== 'object' || Array.isArray(receipt))) {
            return null;
        }

        const hasReceiptId = receipt && Object.prototype.hasOwnProperty.call(receipt, 'answer_query_id');
        const envelopeId = envelope.answer_query_id;
        const receiptId = receipt?.answer_query_id;
        const valid = (value) => typeof value === 'string' && ANSWER_QUERY_ID_PATTERN.test(value);

        if ((hasEnvelopeId && !valid(envelopeId)) || (hasReceiptId && !valid(receiptId))) {
            return null;
        }
        if (hasEnvelopeId && hasReceiptId && envelopeId !== receiptId) return null;
        return hasReceiptId ? receiptId : (hasEnvelopeId ? envelopeId : null);
    }

    function contextLabel(snapshot) {
        if (!snapshot) return null;
        if (snapshot.route === 'work-order.show' && snapshot.record_ids?.length === 1) {
            return 'Job on this page';
        }
        return CONTEXT_LABELS[snapshot.route + '|' + snapshot.kind] || 'Jobs on this page';
    }

    function contextSubject(snapshot) {
        if (!snapshot) return null;
        return CONTEXT_SUBJECTS[snapshot.route + '|' + snapshot.kind] || 'Jobs';
    }

    // Task B7.1 / R2 fix: `recordContext` is the untrusted wire shape Billing already
    // sends the server (mcp-global-search.js buildRecordContext -> {version, origin,
    // query, references[]}) — identifiers only, never a label/display field. Stored
    // as-is (not a parallel shape) so retry/regenerate can re-send the exact refs that
    // grounded the original turn; the server re-resolves + re-authorizes them (B4).
    function makeRetryDescriptor(question, snapshot, operation = 'ask', targetExchangeId = null, recordContext = null) {
        const context = snapshot ? {
            version: snapshot.version,
            context_id: snapshot.context_id,
            route: snapshot.route,
            kind: snapshot.kind,
            resource_type: snapshot.resource_type,
            record_ids: [...(snapshot.record_ids || [])],
            ...(snapshot.scope_id ? { scope_id: snapshot.scope_id } : {}),
        } : null;
        // Task 1B: defensive normalization — an explicit record context always
        // takes precedence over an ambient page snapshot, enforced HERE (not
        // just by the sendTurn caller) so retry, regenerate, and auth-retry
        // descriptors can never carry both fields even if a caller passes a
        // live snapshot unconditionally (e.g. tryAgain()/regenerate() re-derive
        // a fresh snapshot every call).
        const effectiveContext = recordContext ? null : context;
        return deepCloneFreeze({
            question,
            operation,
            target_exchange_id: targetExchangeId,
            context_mode: effectiveContext ? 'jobs' : 'none',
            page_context: effectiveContext,
            context_label: contextLabel(effectiveContext),
            record_context: recordContext || null,
        });
    }

    function loaderStageForMsg() {
        return 'full';
    }

    function loaderSrcForMsg() {
        return FULL_LOADER_SRC;
    }

    function loaderMarkupForMsg() {
        return '<img class="mcp-ask-ai__loader mcp-ask-ai__loader--full" data-loader-stage="full"'
            + ' src="' + FULL_LOADER_SRC + '" width="96" height="96" alt="">';
    }

    function mendMarkdown(text) {
        if (!text || typeof text !== 'string') return '';
        let result = text;

        // 1. Code fences (```)
        const codeFenceMatches = result.match(/```/g) || [];
        if (codeFenceMatches.length % 2 === 1) {
            return result + '\n```';
        }

        // 2. Inline backticks (`) outside code blocks
        const strippedBlocks = result.replace(/```[\s\S]*?```/g, '');
        const inlineBackticks = (strippedBlocks.match(/`/g) || []).length;
        if (inlineBackticks % 2 === 1) {
            result += '`';
        }

        // 3. Bold (**)
        const boldMatches = (result.replace(/```[\s\S]*?```/g, '').match(/\*\*/g) || []).length;
        const hasUnclosedBold = boldMatches % 2 === 1;

        // 4. Italic (*) - single asterisks not part of **
        const strippedBold = result.replace(/```[\s\S]*?```/g, '').replace(/\*\*/g, '');
        const singleAsterisks = (strippedBold.match(/\*/g) || []).length;
        const hasUnclosedItalic = singleAsterisks % 2 === 1;

        if (hasUnclosedItalic) {
            result += '*';
        }
        if (hasUnclosedBold) {
            result += '**';
        }

        // 5. Strikethrough (~~)
        const strikeMatches = (result.replace(/```[\s\S]*?```/g, '').match(/~~/g) || []).length;
        if (strikeMatches % 2 === 1) {
            result += '~~';
        }

        return result;
    }

    function isTransientError(errorText, errorCode) {
        if (!errorText && !errorCode) return false;
        const text = (errorText || '').toLowerCase();
        const code = (errorCode || '').toLowerCase();
        return code === 'service_unavailable'
            || code === 'stream_timeout'
            || code === 'incomplete_stream'
            || text.includes('busy right now')
            || text.includes('taking too long')
            || text.includes('did not finish')
            || text.includes('interrupted')
            || text.includes('wait a moment');
    }

    function displayLabelForMsg(msg) {
        if (!msg?.phases?.length) return msg?.statusLabel || (msg?.status === 'connecting' ? 'Connecting to Ask Assistant...' : 'Understanding your question');
        return msg.phases[msg.phases.length - 1].label;
    }

    function renderThinkingContent(msg, escapeHtml) {
        // The work timeline module shows the turn's progress; the panel draws none.
        if (window.McpAskAiProgressOwner === 'module') return '';
        const label = displayLabelForMsg(msg);
        const phase = msg?.phases?.length ? msg.phases[msg.phases.length - 1].phase : 'connecting';
        const isConnecting = phase === 'connecting' || !msg?.phases?.length;
        const phaseClass = isConnecting ? ' is-connecting mcp-ask-ai__status-badge--connecting' : '';
        if (prefersReducedMotion()) {
            return '<div class="mcp-ask-ai__status-badge' + phaseClass + '" role="status">'
                + '<span class="mcp-ask-ai__thinking-text">' + escapeHtml(label) + '</span>'
                + '<span class="mcp-ask-ai__elapsed" aria-hidden="true"></span>'
                + '</div>';
        }
        return '<div class="mcp-ask-ai__status-badge' + phaseClass + '" role="status">'
            + '<div class="mcp-ask-ai__loading" role="status" aria-live="polite">'
            + loaderMarkupForMsg(msg)
            + '<span class="mcp-ask-ai__loading-label">' + escapeHtml(label) + '</span>'
            + '<span class="mcp-ask-ai__elapsed" aria-hidden="true"></span>'
            + '</div>'
            + '</div>';
    }

    function mapStreamError(payload) {
        const code = payload?.error || '';
        const detail = payload?.detail || '';
        const known = {
            azure_auth: 'AI search could not sign in. Try again or contact support if this persists.',
            conversation_store: 'Chat history is temporarily unavailable.',
            service_unavailable: 'Search is busy right now. Wait a moment and try again.',
            // Task A9 (Bug 1): a PERMANENT capability gap (Phase-0 SQL containment
            // denial), never "busy, try again" — retrying can't fix this. RAG
            // already sends this exact honest copy as `detail`; the literal here
            // is only the safety-net default if `detail` is ever missing.
            capability_unavailable: detail || (
                "I can't answer questions that need live business data yet — I can help "
                + 'with documents, policies, and records you open from Global Search.'
            ),
            incomplete_stream: detail || 'The answer did not finish. Try again.',
            stream_timeout: detail || 'This is taking too long. Try again in a moment.',
            non_streamable_query_type:
                'That question combines document and database lookups. Please try asking it again.',
            internal_error: 'Something went wrong answering your question.',
        };
        if (known[code]) return known[code];
        return detail || 'AI search failed.';
    }

    function unavailableStateHtml() {
        return [
            '<div class="mcp-ask-ai__unavailable">',
            '  <p class="mcp-ask-ai__unavailable-title">AI is currently unavailable</p>',
            '  <p class="mcp-ask-ai__unavailable-hint">Document search is disabled or the service is unreachable. Try again later.</p>',
            '</div>',
        ].join('');
    }

    function emptyStateHtml({ starterPrompts, escapeHtml }) {
        return [
            '<div class="mcp-ask-ai__empty">',
            '  <p class="mcp-ask-ai__empty-title">Ask anything about your business</p>',
            '  <p class="mcp-ask-ai__empty-hint">AI can make mistakes. It uses only data your account can access; verify important information.</p>',
            '  <ul class="mcp-ask-ai__prompts" role="list">',
            (starterPrompts || []).map((p, i) =>
                '    <li><button type="button" class="mcp-ask-ai__prompt" data-prompt-index="' + i + '">' +
                escapeHtml(p) + '</button></li>'
            ).join(''),
            '  </ul>',
            '</div>',
        ].join('');
    }

    function createUi(deps) {
        const {
            escapeHtml,
            getThreadEl,
            scrollToBottom,
            renderMarkdown,
            isSameOriginAppPath,
        } = deps;

        function getAssistantArticle(msg) {
            return getThreadEl()?.querySelector('[data-msg-id="' + msg.id + '"]');
        }

        function updatePhaseTimeline(msg, article) {
            const el = article || getAssistantArticle(msg);
            if (!el) return;
            let ol = el.querySelector('.mcp-ask-ai__phases');
            if (!msg.phases?.length) {
                ol?.remove();
                return;
            }
            if (!ol) {
                ol = document.createElement('ol');
                ol.className = 'mcp-ask-ai__phases';
                ol.setAttribute('aria-label', 'Answer progress');
                const body = el.querySelector('.mcp-ask-ai__msg-body');
                if (body && body.parentNode) body.parentNode.insertBefore(ol, body);
            }
            for (let i = ol.children.length; i < msg.phases.length; i++) {
                const li = document.createElement('li');
                li.textContent = msg.phases[i].label;
                ol.appendChild(li);
            }
        }

        function updateThinkingLoader(msg, bodyEl) {
            const el = bodyEl || msg._bodyEl;
            if (!el) return;

            const label = displayLabelForMsg(msg);
            const phase = msg?.phases?.length ? msg.phases[msg.phases.length - 1].phase : 'connecting';
            const isConnecting = phase === 'connecting' || !msg?.phases?.length;

            let badge = el.querySelector('.mcp-ask-ai__status-badge');
            if (!badge) {
                // A module that owns this turn's progress display removed the
                // badge; rebuilding it here would undo that ownership.
                if (el.closest('[data-progress-owner="module"]') || el.closest('.mcp-ask-ai__msg--v2-work')) return;
                el.innerHTML = renderThinkingContent(msg, escapeHtml);
                return;
            }

            if (isConnecting) {
                badge.classList.add('is-connecting', 'mcp-ask-ai__status-badge--connecting');
            } else {
                badge.classList.remove('is-connecting', 'mcp-ask-ai__status-badge--connecting');
            }

            if (prefersReducedMotion()) {
                let textEl = badge.querySelector('.mcp-ask-ai__thinking-text');
                if (!textEl) {
                    badge.innerHTML = '<span class="mcp-ask-ai__thinking-text">' + escapeHtml(label) + '</span>'
                        + '<span class="mcp-ask-ai__elapsed" aria-hidden="true"></span>';
                } else {
                    textEl.textContent = label;
                }
                return;
            }

            let loading = badge.querySelector('.mcp-ask-ai__loading');
            if (!loading) {
                badge.innerHTML = renderThinkingContent(msg, escapeHtml);
                return;
            }

            const labelEl = loading.querySelector('.mcp-ask-ai__loading-label');
            if (labelEl) labelEl.textContent = label;

            if (!loading.querySelector('.mcp-ask-ai__loader')) {
                loading.insertAdjacentHTML('afterbegin', loaderMarkupForMsg(msg));
            }
        }

        function updateStreamingStatusUI(msg) {
            const article = getAssistantArticle(msg);
            if (!String(msg.content || '').trim()) {
                article?.querySelector('.mcp-ask-ai__phases')?.remove();
                updateThinkingLoader(msg);
            } else {
                updatePhaseTimeline(msg, article);
            }
            scrollToBottom(false);
        }

        function renderPhaseTimeline(msg) {
            if (!msg.phases?.length) return '';
            return '<ol class="mcp-ask-ai__phases" aria-label="Answer progress">' +
                msg.phases.map((phase) => '<li>' + escapeHtml(phase.label) + '</li>').join('') +
                '</ol>';
        }

        function renderAssistantBody(msg, bodyEl) {
            const threadEl = getThreadEl();
            const el = bodyEl || msg._bodyEl || threadEl?.querySelector(
                '[data-msg-id="' + msg.id + '"] .mcp-ask-ai__msg-body'
            );
            if (!el) return null;
            msg._bodyEl = el;
            if (msg.streaming && !String(msg.content || '').trim()) {
                el.innerHTML = renderThinkingContent(msg, escapeHtml);
            } else if (msg.streaming) {
                paintStreamingProse(msg, el);
            } else {
                if (msg._proseFrame) {
                    cancelAnimationFrame(msg._proseFrame);
                    msg._proseFrame = null;
                    msg._proseDirty = false;
                }
                el.innerHTML = renderAnswerContent(msg);
            }
            return el;
        }

        function updateAssistantBubble(msg) {
            renderAssistantBody(msg);
            if (msg.streaming && String(msg.content || '').trim()) {
                updatePhaseTimeline(msg);
            }
            scrollToBottom(false);
        }

        function renderUserMessageHtml(msg) {
            return '<div class="mcp-ask-ai__msg mcp-ask-ai__msg--user" data-msg-id="' + msg.id + '">' +
                '<div class="mcp-ask-ai__bubble">' + escapeHtml(msg.content) + '</div></div>';
        }

        function renderStreamingAssistantHtml(msg) {
            return [
                '<article class="mcp-ask-ai__msg mcp-ask-ai__msg--assistant" data-msg-id="' + msg.id + '" aria-label="AI answer">',
                '<div class="mcp-ask-ai__slot" data-slot="work"></div>',
                '<div class="mcp-ask-ai__slot" data-slot="result"></div>',
                '<div class="mcp-ask-ai__slot" data-slot="answer">',
                '  <div class="mcp-ask-ai__msg-body mcp-ask-ai__msg-body--plain">' +
                    renderThinkingContent(msg, escapeHtml) + '</div>',
                '</div>',
                '<div class="mcp-ask-ai__slot" data-slot="references"></div>',
                '<div class="mcp-ask-ai__slot" data-slot="feedback"></div>',
                '<div class="mcp-ask-ai__slot" data-slot="interaction"></div>',
                '</article>',
            ].join('');
        }

        function appendTurnPair(userMsg, assistantMsg) {
            const threadEl = getThreadEl();
            if (!threadEl) return false;

            // First turn used to bail to renderThread() (full innerHTML). That path
            // left <object> loaders stuck as empty HTML documents; insertAdjacentHTML
            // (and now <img>) paints reliably on the cold empty-state path too.
            threadEl.querySelector('.mcp-ask-ai__empty')?.remove();
            threadEl.insertAdjacentHTML(
                'beforeend',
                renderUserMessageHtml(userMsg) + renderStreamingAssistantHtml(assistantMsg)
            );
            assistantMsg._bodyEl = threadEl.querySelector(
                '[data-msg-id="' + assistantMsg.id + '"] .mcp-ask-ai__msg-body'
            );
            scrollToBottom(true);
            return true;
        }

        function rebindStreamingBodyEls(messages) {
            const threadEl = getThreadEl();
            messages.forEach((msg) => {
                if (msg.role === 'assistant' && msg.streaming) {
                    msg._bodyEl = threadEl?.querySelector(
                        '[data-msg-id="' + msg.id + '"] .mcp-ask-ai__msg-body'
                    ) || null;
                } else if (msg._bodyEl) {
                    delete msg._bodyEl;
                }
            });
        }

        function thumbAttrs(msg, direction) {
            const selected = msg.feedback === direction;
            return {
                pressed: selected ? 'true' : 'false',
                className: 'mcp-ask-ai__thumb mcp-ask-ai__thumb--' + direction +
                    (selected ? ' is-selected' : ''),
            };
        }

        function hasVisibleLabel(link) {
            return typeof link.label === 'string' && link.label.trim() !== '';
        }

        function safeRecordLinks(msg) {
            const isSafe = isSameOriginAppPath || (() => false);
            return (Array.isArray(msg.recordLinks) ? msg.recordLinks : [])
                .filter((link) => link && typeof link.url === 'string' && isSafe(link.url)
                    && hasVisibleLabel(link));
        }

        function placeLabelLinks(root, candidates, opts) {
            const idOf = opts.idOf;
            const buildAnchor = opts.buildAnchor;
            const isWordChar = (ch) => !!ch && /[\p{L}\p{N}]/u.test(ch);
            const placed = new Set();

            const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
                acceptNode(node) {
                    if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
                    if (node.parentElement?.closest('a, code, pre, .mcp-ask-ai__citation')) {
                        return NodeFilter.FILTER_REJECT;
                    }
                    return NodeFilter.FILTER_ACCEPT;
                },
            });
            const textNodes = [];
            while (walker.nextNode()) textNodes.push(walker.currentNode);

            for (const node of textNodes) {
                let current = node;
                // A single text node may hold several distinct labels; keep scanning the
                // tail after each replacement. Earliest match wins; ties break on length.
                for (;;) {
                    const remaining = candidates.filter((link) => !placed.has(idOf(link)));
                    if (!remaining.length) break;
                    let best = null;
                    for (const link of remaining) {
                        const label = link.label;
                        const idx = current.nodeValue.indexOf(label);
                        if (idx === -1) continue;
                        if (isWordChar(current.nodeValue[idx - 1])
                            || isWordChar(current.nodeValue[idx + label.length])) continue;
                        if (!best || idx < best.index
                            || (idx === best.index && label.length > best.link.label.length)) {
                            best = { link, index: idx };
                        }
                    }
                    if (!best) break;
                    const label = best.link.label;
                    const labelNode = current.splitText(best.index);
                    const tailNode = labelNode.splitText(label.length);
                    const anchor = buildAnchor(best.link, label);
                    labelNode.parentNode.replaceChild(anchor, labelNode);
                    placed.add(idOf(best.link));
                    current = tailNode;
                }
            }
            return placed;
        }

        // Wrap the first boundary-safe occurrence of each record's label in the answer
        // prose with an inline app link (Issue 3: mentions linked in place, "like
        // invoices"). Mutates `fragment` in place. The full list of links renders
        // separately from `msg.recordLinks`, so no placement record is kept.
        function linkifyRecordLinks(fragment, msg) {
            const links = safeRecordLinks(msg);
            // A prose candidate needs a letter: a numeric-only label must never
            // link an amount in the body text.
            const candidates = links
                .filter((link) => (link.label || '').trim().length >= 2 && /\p{L}/u.test(link.label || ''))
                .sort((a, b) => (b.label || '').length - (a.label || '').length);
            if (!candidates.length) return;
            placeLabelLinks(fragment, candidates, {
                idOf: (link) => link.url,
                buildAnchor(link, label) {
                    const anchor = document.createElement('a');
                    anchor.className = 'mcp-ask-ai__inline-record' + (link.preview ? ' mcp-ask-ai__record-chip' : '');
                    anchor.setAttribute('href', link.url);
                    anchor.textContent = label;
                    openInNewTab(anchor, label);
                    if (link.preview) {
                        anchor.setAttribute('data-preview', JSON.stringify(link.preview));
                    }
                    return anchor;
                },
            });
        }

        function safeUiLinks(msg) {
            const isSafe = isSameOriginAppPath || (() => false);
            return (Array.isArray(msg.uiLinks) ? msg.uiLinks : [])
                .filter((link) => link && typeof link.url === 'string' && isSafe(link.url));
        }

        function linkifyUiLinks(fragment, msg) {
            const links = safeUiLinks(msg);
            const candidates = links
                .filter((link) => (link.label || '').trim().length >= 2 && /\p{L}/u.test(link.label || ''))
                .sort((a, b) => (b.label || '').length - (a.label || '').length);
            if (!candidates.length) {
                return;
            }
            placeLabelLinks(fragment, candidates, {
                idOf: (link) => link.key,
                buildAnchor(link, label) {
                    const anchor = document.createElement('a');
                    anchor.className = 'mcp-ask-ai__inline-page';
                    anchor.setAttribute('href', link.url);
                    anchor.textContent = label;
                    openInNewTab(anchor, label);
                    return anchor;
                },
            });
        }

        // The turn's full reference list: every safe, labelled record link, one
        // per url, in arrival order — placed inline in the prose or not. A
        // native <details> keeps it collapsed by default, including after a
        // reload or a restored thread, since no open state is persisted.
        function renderRecordLinks(msg) {
            if (!answerPartEnabled('recordRefs')) return '';
            const seenUrls = new Set();
            const links = safeRecordLinks(msg).filter((link) => {
                if (seenUrls.has(link.url)) return false;
                seenUrls.add(link.url);
                return true;
            });
            if (!links.length) return '';
            const items = links.map((link) => {
                const type = recordTypeLabel(link.table);
                return '<li><a class="mcp-ask-ai__inline-record' +
                    (link.preview ? ' mcp-ask-ai__record-chip' : '') +
                    '" href="' + escapeHtml(link.url) + '"' +
                    (link.preview ? ' data-preview="' + escapeHtml(JSON.stringify(link.preview)) + '"' : '') + '>' +
                    (type ? '<span class="mcp-ask-ai__record-ref-type">' + escapeHtml(type) + '</span> ' : '') +
                    escapeHtml(link.label) + '</a></li>';
            }).join('');
            return '<details class="mcp-ask-ai__record-refs">' +
                '<summary class="mcp-ask-ai__record-refs-summary">Referenced records (' +
                escapeHtml(String(links.length)) + ')</summary>' +
                '<ul class="mcp-ask-ai__record-refs-list">' + items + '</ul>' +
                '</details>';
        }

        function renderCitedSources(msg) {
            const hasDocumentSources = Array.isArray(msg.sources) && msg.sources.length > 0;
            if (hasDocumentSources && msg.citations?.parsed === false) {
                return [
                    '<aside class="mcp-ask-ai__citation-note" role="note" aria-label="Source link status">',
                    '  <i class="fa fa-circle-info" aria-hidden="true"></i>',
                    '  <span><strong>Source links unavailable</strong>',
                    '  This answer used company documents, but individual claims could not be linked automatically.</span>',
                    '</aside>',
                ].join('');
            }
            return '';
        }

        function renderDisambiguationCardHtml(msg) {
            const dis = msg.disambiguation;
            if (!dis || !Array.isArray(dis.candidates) || !dis.candidates.length) return '';
            const term = dis.term || '';
            const candidates = dis.candidates;

            return [
                '<div class="mcp-ask-ai__disambiguation-card" role="region" aria-label="Disambiguation choices">',
                '  <p class="mcp-ask-ai__disambiguation-title">',
                '    <i class="fa fa-circle-question" aria-hidden="true"></i> Multiple matches found for <strong>' + escapeHtml(term) + '</strong>. Did you mean:',
                '  </p>',
                '  <div class="mcp-ask-ai__disambiguation-pills">',
                candidates.map((c, i) => {
                    const label = c.label || c.id || '';
                    const sec = c.secondary_label ? '<span class="mcp-ask-ai__disambiguation-secondary">' + escapeHtml(c.secondary_label) + '</span>' : '';
                    const query = c.suggested_query || label;
                    return '<button type="button" class="mcp-btn mcp-btn--secondary mcp-btn--sm mcp-ask-ai__disambiguation-pill" ' +
                        'data-disambiguate="' + escapeHtml(query) + '" data-candidate-index="' + i + '">' +
                        '<strong>' + escapeHtml(label) + '</strong>' + (sec ? ' ' + sec : '') +
                        '</button>';
                }).join(''),
                '  </div>',
                '</div>',
            ].join('');
        }

        function safeDomId(value) {
            return String(value || 'citation').replace(/[^A-Za-z0-9_-]/g, '-');
        }

        function documentEvidence(msg) {
            const sources = new Map((msg.sources || []).map((source) => [String(source.id), source]));
            const evidence = new Map();
            (msg.citations?.cited || []).forEach((citation) => {
                const marker = String(citation.marker);
                const source = sources.get(String(citation.id));
                if (!source || evidence.has(marker)) return;
                evidence.set(marker, {
                    marker,
                    type: 'Document',
                    title: source.title || 'Company document',
                    section: source.section || '',
                    snippet: source.snippet || '',
                    links: [],
                });
            });
            return evidence;
        }

        // The heading path inside the document: the section without the
        // document title. The source card module (source_card.js) uses the same rule.
        function sectionHeading(section, title) {
            const parts = String(section || '').split(' > ').map((part) => part.trim()).filter(Boolean);
            if (parts[0] === title) parts.shift();
            return parts.join(' > ');
        }

        // Corpus text goes into the card as text nodes, never as markup.
        function fillCitationCard(fragment, evidence) {
            const texts = [
                ['.mcp-ask-ai__citation-title', evidence.title],
                ['.mcp-ask-ai__citation-heading', sectionHeading(evidence.section, evidence.title)],
                ['.mcp-ask-ai__citation-snippet', evidence.snippet],
            ];
            for (const [selector, text] of texts) {
                const node = fragment.querySelector(selector);
                if (!node) continue;
                if (text) node.textContent = text;
                else node.remove();
            }
        }

        function citationCardHtml(msg, evidence, occurrence) {
            const marker = String(evidence.marker);
            const cardId = 'mcp-citation-' + safeDomId(msg.id) + '-' + safeDomId(marker) + '-' + occurrence;
            const isSafe = isSameOriginAppPath || (() => true);
            const safeLinks = (evidence.links || []).filter((link) => isSafe(link?.url));
            const visibleLinks = safeLinks.slice(0, 5);
            const linksHtml = visibleLinks.length
                ? '<span class="mcp-ask-ai__citation-links">' +
                    visibleLinks.map((link) => {
                        const text = escapeHtml(link.label || (link.table + ' #' + link.record_id));
                        return '<a href="' + escapeHtml(link.url) + '" target="_blank" rel="noopener"'
                            + ' aria-label="' + text + ' (opens in a new tab)">' + text + '</a>';
                    }).join('') +
                    (safeLinks.length > visibleLinks.length
                        ? '<span class="mcp-ask-ai__citation-more">+' +
                            escapeHtml(String(safeLinks.length - visibleLinks.length)) + ' more referenced records</span>'
                        : '') + '</span>'
                : '';
            return [
                '<span class="mcp-ask-ai__citation" data-citation>',
                '  <button type="button" class="mcp-ask-ai__citation-marker" data-citation-marker="' +
                    escapeHtml(marker) + '" aria-label="Show source ' + escapeHtml(marker) +
                    '" aria-expanded="false" aria-controls="' + cardId + '">[' +
                    escapeHtml(marker) + ']</button>',
                '  <span class="mcp-ask-ai__citation-card" id="' + cardId +
                    '" role="dialog" aria-label="Source ' + escapeHtml(marker) + ' details" aria-hidden="true">',
                '    <span class="mcp-ask-ai__citation-type">' + escapeHtml(evidence.type) + '</span>',
                '    <strong class="mcp-ask-ai__citation-title"></strong>',
                '    <span class="mcp-ask-ai__citation-heading"></span>',
                '    <span class="mcp-ask-ai__citation-snippet"></span>',
                linksHtml,
                '  </span>',
                '</span>',
            ].join('');
        }

        function decorateTables(fragment, msg) {
            fragment.querySelectorAll('table').forEach((table) => {
                table.classList.add('mcp-table', 'mcp-ask-ai__table');
                let wrapper = table.closest('.mcp-ask-ai__table-wrap');
                if (!wrapper) {
                    wrapper = document.createElement('div');
                    wrapper.className = 'mcp-ask-ai__table-wrap';
                    table.parentNode.insertBefore(wrapper, table);
                    wrapper.appendChild(table);
                }
                if (msg && !msg.streaming) {
                    if (!wrapper.querySelector('.mcp-ask-ai__table-actions')) {
                        const actions = document.createElement('div');
                        actions.className = 'mcp-ask-ai__table-actions';
                        actions.innerHTML = [
                            '<button type="button" class="mcp-btn mcp-btn--xs mcp-btn--secondary mcp-ask-ai__table-btn" data-action="copy-table">',
                            '  <i class="fa fa-copy" aria-hidden="true"></i> Copy as Table',
                            '</button>',
                            '<button type="button" class="mcp-btn mcp-btn--xs mcp-btn--secondary mcp-ask-ai__table-btn" data-action="export-csv">',
                            '  <i class="fa fa-download" aria-hidden="true"></i> Export to CSV',
                            '</button>',
                        ].join('');
                        wrapper.insertBefore(actions, table);
                    }
                }
            });
        }

        // One assembly order for every paint, live and restored:
        // (a) Markdown -> DOM through the sanitizer allowlist, (b) citation
        // markers resolved or removed on a text-node walk. Record links and
        // tables finalise after this in the terminal paint only.
        function renderProse(msg, { streaming = false } = {}) {
            const text = displayAnswerContent(msg);
            const template = document.createElement('template');
            template.innerHTML = renderMarkdown(streaming ? mendMarkdown(text) : text);
            replaceCitationMarkers(template.content, msg);
            return template;
        }

        function replaceCitationMarkers(root, msg) {
            const evidence = documentEvidence(msg);
            const markerToken = /\[(?:Source\s+)?(\d+)\]/gi;
            const occurrences = new Map();
            const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
                acceptNode(node) {
                    if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
                    // Never rewrite a marker inside a link, code, or a citation card.
                    if (node.parentElement?.closest('a, code, pre, .mcp-ask-ai__citation')) {
                        return NodeFilter.FILTER_REJECT;
                    }
                    return NodeFilter.FILTER_ACCEPT;
                },
            });
            const textNodes = [];
            while (walker.nextNode()) textNodes.push(walker.currentNode);

            for (const node of textNodes) {
                let current = node;
                while (current) {
                    markerToken.lastIndex = 0;
                    const match = markerToken.exec(current.nodeValue);
                    if (!match) break;
                    const marker = match[1];
                    const value = current.nodeValue;
                    const tail = current.splitText(match.index + match[0].length);
                    current.nodeValue = value.slice(0, match.index);
                    const source = evidence.get(marker);
                    if (source) {
                        const occurrence = (occurrences.get(marker) || 0) + 1;
                        occurrences.set(marker, occurrence);
                        const holder = document.createElement('template');
                        holder.innerHTML = citationCardHtml(msg, source, occurrence);
                        fillCitationCard(holder.content, source);
                        current.parentNode.insertBefore(holder.content, tail);
                    }
                    current = tail.nodeValue ? tail : null;
                }
            }
        }

        // Streaming paint coalesced to one parse per animation frame per message;
        // a throw falls back to today's escaped-text path, never a blank bubble.
        function paintStreamingProse(msg, el) {
            el.classList.remove('mcp-ask-ai__msg-body--plain');
            if (msg._proseFrame) {
                msg._proseDirty = true;
                return;
            }
            msg._proseFrame = requestAnimationFrame(() => {
                msg._proseFrame = null;
                msg._proseDirty = false;
                try {
                    const fragment = renderProse(msg, { streaming: true }).content;
                    el.innerHTML = '';
                    el.appendChild(fragment);
                } catch (err) {
                    el.classList.add('mcp-ask-ai__msg-body--plain');
                    el.textContent = msg.content;
                }
            });
        }

        function renderAnswerContent(msg) {
            const template = renderProse(msg, { streaming: false });
            decorateTables(template.content, msg);
            linkifyRecordLinks(template.content, msg);
            linkifyUiLinks(template.content, msg);
            return template.innerHTML;
        }

        function renderAssistantMessageHtml(msg, { lastCompleted, threadState }) {

            // The plain class keeps the thinking placeholder readable; parsed
            // markdown brings its own block layout, so it paints without it.
            const streamingWithContent = msg.streaming && String(msg.content || '').trim();
            const bodyClass = 'mcp-ask-ai__msg-body' + (msg.streaming && !streamingWithContent
                ? ' mcp-ask-ai__msg-body--plain' : '');
            let content;
            if (msg.streaming && !String(msg.content || '').trim()) {
                content = renderThinkingContent(msg, escapeHtml);
            } else if (msg.streaming) {
                content = renderProse(msg, { streaming: true }).innerHTML;
            } else {
                content = renderAnswerContent(msg);
            }

            const clarification = msg.outcome === 'clarification_required';

            let actions = '';
            if (!clarification && !msg.streaming && msg.role === 'assistant' && !msg.error) {
                const up = thumbAttrs(msg, 'up');
                const down = thumbAttrs(msg, 'down');
                actions = [
                    '<div class="mcp-ask-ai__actions">',
                    '<button type="button" class="mcp-ask-ai__thumb" data-copy="' + msg.id +
                        '" aria-label="Copy answer" data-mcp-tip="Copy">' +
                        '<i class="fa fa-copy" aria-hidden="true"></i></button>',
                    msg.traceId && msg.feedbackToken
                        ? '<button type="button" class="' + up.className +
                            '" data-feedback="up" data-msg-id="' + msg.id +
                            '" aria-pressed="' + up.pressed +
                            '" aria-label="Helpful answer" data-mcp-tip="Helpful">' +
                            '<i class="fa fa-thumbs-up" aria-hidden="true"></i></button>'
                        : '',
                    msg.traceId && msg.feedbackToken
                        ? '<button type="button" class="' + down.className +
                            '" data-feedback="down" data-msg-id="' + msg.id +
                            '" aria-pressed="' + down.pressed +
                            '" aria-label="Not helpful" data-mcp-tip="Not helpful">' +
                            '<i class="fa fa-thumbs-down" aria-hidden="true"></i></button>'
                        : '',
                    msg === lastCompleted && msg.exchangeId
                        ? '<button type="button" class="mcp-ask-ai__thumb" data-regenerate="' + msg.id +
                            '" aria-label="Regenerate answer" data-mcp-tip="Regenerate">' +
                            '<i class="fa fa-refresh" aria-hidden="true"></i></button>' : '',
                    '</div>',
                ].join('');
            }

            // Task B7 / R4: follow-up chips gate on the explicit "ready" state,
            // not a raw boolean — a restored-but-unconfirmed ("resuming") thread
            // does not resurrect a stale capability signal (see mcp-ask-ai.js).
            let chipsHtml = '';
            if (!clarification && msg === lastCompleted && threadState === 'ready') {
                const normalize = window.McpAskAiPersist?.normalizeFollowupSuggestions;
                const prompts = normalize
                    ? normalize(msg.followupSuggestions)
                    : (Array.isArray(msg.followupSuggestions) ? msg.followupSuggestions.slice(0, 3) : []);
                chipsHtml = prompts.length
                    ? '<div class="mcp-ask-ai__chips">' + prompts.map((p, i) =>
                        '<button type="button" class="mcp-ask-ai__chip mcp-ask-ai__follow-up-pill" data-chip-index="' + i
                        + '" data-chip-message-id="' + msg.id + '">'
                        + '<span class="mcp-ask-ai__chip-icon" aria-hidden="true"><i class="fa fa-comment-dots"></i></span>'
                        + escapeHtml(p) + '<i class="fa fa-arrow-right mcp-ask-ai__chip-arrow" aria-hidden="true"></i>'
                        + '</button>').join('') + '</div>'
                    : '';
            }

            const notices = [
                // Task A9 (Bug 2): an honest, non-error notice — the turn itself
                // still succeeded, so this must never suppress feedback/regenerate
                // affordances (those gate on msg.error, untouched here).
                msg.threadExpiredNotice
                    ? '<p class="mcp-ask-ai__thread-notice">' + escapeHtml(msg.threadExpiredNotice) + '</p>'
                    : '',
                msg.stopped ? '<p class="mcp-ask-ai__stopped">Stopped</p>' +
                    (msg.retryDescriptor ? '<button type="button" class="mcp-btn mcp-btn--secondary mcp-btn--sm mcp-ask-ai__try-again" data-try-again="' +
                        msg.id + '"><i class="fa fa-rotate-right" aria-hidden="true"></i>Try again</button>' : '') : '',
                msg.error && isTransientError(msg.error, msg.errorCode)
                    ? '<div class="mcp-ask-ai__notice mcp-ask-ai__notice--warning" role="alert">' +
                        '<span class="mcp-ask-ai__notice-icon" aria-hidden="true"><i class="fa fa-triangle-exclamation"></i></span>' +
                        '<div class="mcp-ask-ai__notice-body">' +
                        '<p class="mcp-ask-ai__notice-text">' + escapeHtml(msg.error) + '</p>' +
                        (msg.retryDescriptor ? '<button type="button" class="mcp-btn mcp-btn--secondary mcp-btn--sm mcp-ask-ai__try-again" data-try-again="' +
                            msg.id + '"><i class="fa fa-rotate-right" aria-hidden="true"></i>Try again</button>' : '') +
                        '</div></div>'
                    : (msg.error ? '<p class="mcp-ask-ai__error">' + escapeHtml(msg.error) + '</p>' +
                        (msg.retryDescriptor ? '<button type="button" class="mcp-btn mcp-btn--secondary mcp-btn--sm mcp-ask-ai__try-again" data-try-again="' +
                            msg.id + '"><i class="fa fa-rotate-right" aria-hidden="true"></i>Try again</button>' : '') : ''),
            ].join('');

            return [
                '<article class="mcp-ask-ai__msg mcp-ask-ai__msg--assistant" data-msg-id="' + msg.id + '" aria-label="AI answer">',
                '<div class="mcp-ask-ai__slot" data-slot="work"></div>',
                '<div class="mcp-ask-ai__slot" data-slot="result"></div>',
                '<div class="mcp-ask-ai__slot" data-slot="answer">',
                msg.streaming && String(msg.content || '').trim() ? renderPhaseTimeline(msg) : '',
                '  <div class="' + bodyClass + '">' + content + '</div>',
                notices,
                '</div>',
                '<div class="mcp-ask-ai__slot" data-slot="references">' + (msg.streaming ? '' : renderRecordLinks(msg)) + renderCitedSources(msg) + '</div>',
                '<div class="mcp-ask-ai__slot" data-slot="feedback">' + actions + '</div>',
                '<div class="mcp-ask-ai__slot" data-slot="interaction">' + renderDisambiguationCardHtml(msg) + chipsHtml + '</div>',
                '</article>',
            ].join('');
        }

        function renderMessagesHtml({ messages, threadState }) {
            const lastCompleted = [...messages].reverse().find(
                (m) => m.role === 'assistant' && !m.streaming && !m.error
            );
            return messages.map((msg) => {
                if (msg.role === 'user') return renderUserMessageHtml(msg);
                return renderAssistantMessageHtml(msg, {
                    lastCompleted,
                    threadState,
                });
            }).join('');
        }

        function bindThreadInteractions(handlers) {
            const thread = getThreadEl();
            if (!thread) return;
            thread.querySelectorAll('[data-feedback]').forEach((btn) => {
                btn.addEventListener('click', () => handlers.onFeedback?.(
                    btn.getAttribute('data-msg-id'),
                    btn.getAttribute('data-feedback')
                ));
            });
            thread.querySelectorAll('[data-try-again]').forEach((btn) => {
                btn.addEventListener('click', () => handlers.onTryAgain?.(btn.getAttribute('data-try-again')));
            });
            thread.querySelectorAll('[data-regenerate]').forEach((btn) => {
                btn.addEventListener('click', () => handlers.onRegenerate?.(btn.getAttribute('data-regenerate')));
            });
            thread.querySelectorAll('[data-copy]').forEach((btn) => {
                btn.addEventListener('click', () => {
                    if (restoreCopyBlocked()) return;
                    handlers.onCopy?.(btn.getAttribute('data-copy'), btn);
                });
            });
            thread.querySelectorAll('[data-copy-answer-query-id]').forEach((btn) => {
                btn.addEventListener('click', () => {
                    if (restoreCopyBlocked()) return;
                    const answerQueryId = btn.getAttribute('data-copy-answer-query-id');
                    if (!answerQueryId) return;
                    copyTextToClipboard(answerQueryId).then(() => {
                        const original = btn.innerHTML;
                        btn.innerHTML = '<i class="fa fa-check" aria-hidden="true"></i> Copied!';
                        setTimeout(() => { btn.innerHTML = original; }, 1500);
                    }).catch(() => {});
                });
            });
            function closeCitation(citation, restoreFocus = false) {
                citation.classList.remove('is-open');
                citation.dataset.pinned = 'false';
                const button = citation.querySelector('[data-citation-marker]');
                const card = citation.querySelector('.mcp-ask-ai__citation-card');
                button?.setAttribute('aria-expanded', 'false');
                card?.setAttribute('aria-hidden', 'true');
                if (restoreFocus) button?.focus();
            }

            function positionCitation(citation) {
                const button = citation.querySelector('[data-citation-marker]');
                const card = citation.querySelector('.mcp-ask-ai__citation-card');
                if (!button || !card) return;
                const margin = 12;
                const width = Math.min(320, Math.max(240, window.innerWidth - (margin * 2)));
                card.style.width = width + 'px';
                const buttonRect = button.getBoundingClientRect();
                const citationRect = citation.getBoundingClientRect();
                const threadRect = thread.getBoundingClientRect();
                card.style.left = '0px';
                card.style.top = '0px';
                const cardRect = card.getBoundingClientRect();
                const leftBoundary = Math.max(margin, threadRect.left + margin);
                const rightBoundary = Math.min(
                    window.innerWidth - margin,
                    threadRect.right - margin
                );
                const left = Math.min(
                    Math.max(leftBoundary, buttonRect.left - 20),
                    Math.max(leftBoundary, rightBoundary - width)
                );
                const topBoundary = Math.max(margin, threadRect.top + margin);
                const bottomBoundary = Math.min(
                    window.innerHeight - margin,
                    threadRect.bottom - margin
                );
                let top = buttonRect.bottom + 8;
                if (top + cardRect.height > bottomBoundary) {
                    top = Math.max(topBoundary, buttonRect.top - cardRect.height - 8);
                }
                card.style.left = (left - citationRect.left) + 'px';
                card.style.top = (top - citationRect.top) + 'px';
            }

            function openCitation(citation, pinned = false) {
                thread.querySelectorAll('[data-citation].is-open').forEach((open) => {
                    if (open !== citation) closeCitation(open);
                });
                citation.classList.add('is-open');
                if (pinned) citation.dataset.pinned = 'true';
                const button = citation.querySelector('[data-citation-marker]');
                const card = citation.querySelector('.mcp-ask-ai__citation-card');
                button?.setAttribute('aria-expanded', 'true');
                card?.setAttribute('aria-hidden', 'false');
                positionCitation(citation);
            }

            thread.querySelectorAll('[data-citation]').forEach((citation) => {
                const button = citation.querySelector('[data-citation-marker]');
                citation.dataset.pinned = 'false';
                citation.addEventListener('pointerenter', () => openCitation(citation));
                citation.addEventListener('pointerleave', () => {
                    if (citation.dataset.pinned !== 'true') closeCitation(citation);
                });
                citation.addEventListener('focusin', () => openCitation(citation));
                citation.addEventListener('focusout', (event) => {
                    if (!citation.contains(event.relatedTarget) && citation.dataset.pinned !== 'true') {
                        closeCitation(citation);
                    }
                });
                citation.addEventListener('keydown', (event) => {
                    if (event.key === 'Escape' && citation.classList.contains('is-open')) {
                        event.preventDefault();
                        event.stopPropagation();
                        closeCitation(citation, true);
                    }
                });
                button?.addEventListener('click', () => {
                    if (citation.dataset.pinned === 'true') {
                        closeCitation(citation, true);
                    } else {
                        openCitation(citation, true);
                    }
                });
            });
            thread.querySelectorAll('.mcp-ask-ai__chip').forEach((btn) => {
                btn.addEventListener('click', () => {
                    const idx = parseInt(btn.getAttribute('data-chip-index'), 10);
                    handlers.onChip?.(btn.getAttribute('data-chip-message-id'), idx);
                });
            });
            thread.querySelectorAll('pre code').forEach((block) => {
                block.parentElement?.classList.add('mcp-ask-ai__code-block');
            });

            function closeAllHoverCards() {
                thread.querySelectorAll('.mcp-ask-ai__hover-card').forEach((el) => el.remove());
            }

            function showHoverCard(chip) {
                closeAllHoverCards();
                const raw = chip.getAttribute('data-preview');
                if (!raw) return;
                let preview = null;
                try {
                    preview = JSON.parse(raw);
                } catch (_) {
                    return;
                }
                if (!preview || typeof preview !== 'object') return;

                const card = document.createElement('div');
                card.className = 'mcp-ask-ai__hover-card';
                card.setAttribute('role', 'tooltip');
                card.setAttribute('aria-expanded', 'true');

                const header = document.createElement('div');
                header.className = 'mcp-ask-ai__hover-card-header';

                const title = document.createElement('strong');
                title.className = 'mcp-ask-ai__hover-card-title';
                title.textContent = preview.title || '';
                header.appendChild(title);

                if (preview.status) {
                    const badge = document.createElement('span');
                    const variant = preview.badge_variant || 'neutral';
                    badge.className = 'mcp-ask-ai__badge mcp-ask-ai__badge--' + variant;
                    badge.textContent = preview.status;
                    header.appendChild(badge);
                }
                card.appendChild(header);

                if (preview.subtitle) {
                    const sub = document.createElement('div');
                    sub.className = 'mcp-ask-ai__hover-card-subtitle';
                    sub.textContent = preview.subtitle;
                    card.appendChild(sub);
                }

                if (preview.amount) {
                    const amt = document.createElement('div');
                    amt.className = 'mcp-ask-ai__hover-card-amount';
                    amt.textContent = preview.amount;
                    card.appendChild(amt);
                }

                if (preview.updated_at) {
                    const updated = document.createElement('div');
                    updated.className = 'mcp-ask-ai__hover-card-updated';
                    updated.textContent = 'Updated ' + preview.updated_at;
                    card.appendChild(updated);
                }

                const actions = document.createElement('div');
                actions.className = 'mcp-ask-ai__hover-card-actions';

                const openBtn = document.createElement('a');
                openBtn.className = 'mcp-btn mcp-btn--sm mcp-btn--primary';
                openBtn.setAttribute('data-action', 'open-record');
                openBtn.setAttribute('href', chip.getAttribute('href') || '#');
                openBtn.textContent = 'Open Record';
                openInNewTab(openBtn, 'Open record');
                actions.appendChild(openBtn);

                const copyBtn = document.createElement('button');
                copyBtn.type = 'button';
                copyBtn.className = 'mcp-btn mcp-btn--sm mcp-btn--secondary';
                copyBtn.setAttribute('data-action', 'copy-link');
                copyBtn.textContent = 'Copy Link';
                copyBtn.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    const url = chip.getAttribute('href');
                    if (url) {
                        copyTextToClipboard(url).then(() => {
                            copyBtn.textContent = 'Copied!';
                            setTimeout(() => { copyBtn.textContent = 'Copy Link'; }, 1500);
                        }).catch(() => {});
                    }
                });
                actions.appendChild(copyBtn);
                card.appendChild(actions);

                chip.style.position = 'relative';
                chip.appendChild(card);
            }

            thread.querySelectorAll('.mcp-ask-ai__record-chip').forEach((chip) => {
                chip.addEventListener('mouseenter', () => showHoverCard(chip));
                chip.addEventListener('mouseleave', () => closeAllHoverCards());
                chip.addEventListener('focus', () => showHoverCard(chip));
                chip.addEventListener('blur', (e) => {
                    if (!chip.contains(e.relatedTarget)) closeAllHoverCards();
                });
            });

            thread.querySelectorAll('.mcp-ask-ai__trust-toggle').forEach((btn) => {
                btn.addEventListener('click', () => {
                    const drawerId = btn.getAttribute('aria-controls');
                    const drawer = drawerId ? thread.querySelector('#' + drawerId) : null;
                    if (!drawer) return;
                    const isExpanded = btn.getAttribute('aria-expanded') === 'true';
                    btn.setAttribute('aria-expanded', isExpanded ? 'false' : 'true');
                    drawer.hidden = isExpanded;
                });
            });

            function extractTableData(table) {
                const rows = [];
                table.querySelectorAll('tr').forEach((tr) => {
                    const row = [];
                    tr.querySelectorAll('th, td').forEach((cell) => {
                        row.push(cell.textContent.trim());
                    });
                    if (row.length > 0) rows.push(row);
                });
                return rows;
            }

            function formatTableAsTsv(rows) {
                return rows.map((row) => row.join('\t')).join('\n') + '\n';
            }

            function sanitizeCsvCell(rawCell) {
                let cell = String(rawCell ?? '');
                const trimmed = cell.trimStart();
                const formulaPrefixes = ['=', '+', '-', '@', '\t', '\r', '|', '%'];
                let formulaEscaped = false;
                if (trimmed.length > 0 && formulaPrefixes.includes(trimmed[0])) {
                    cell = "'" + trimmed;
                    formulaEscaped = true;
                }
                if (formulaEscaped || cell.includes(',') || cell.includes('"') || cell.includes('\n') || cell.includes('\r')) {
                    return '"' + cell.replace(/"/g, '""') + '"';
                }
                return cell;
            }

            function formatTableAsCsv(rows) {
                return rows.map((row) => row.map(sanitizeCsvCell).join(',')).join('\r\n');
            }

            function downloadCsv(csvString, filename = 'table-export.csv') {
                const blob = new Blob(['\uFEFF', csvString], { type: 'text/csv;charset=utf-8;' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
            }

            thread.querySelectorAll('.mcp-ask-ai__table-wrap').forEach((wrapper) => {
                const table = wrapper.querySelector('table');
                if (!table) return;

                wrapper.querySelector('[data-action="copy-table"]')?.addEventListener('click', (e) => {
                    e.preventDefault();
                    if (restoreCopyBlocked()) return;
                    const rows = extractTableData(table);
                    const tsv = formatTableAsTsv(rows);
                    copyTextToClipboard(tsv).then(() => {
                        const btn = e.currentTarget;
                        const original = btn.innerHTML;
                        btn.innerHTML = '<i class="fa fa-check" aria-hidden="true"></i> Copied!';
                        setTimeout(() => { btn.innerHTML = original; }, 1500);
                    }).catch(() => {});
                });

                wrapper.querySelector('[data-action="export-csv"]')?.addEventListener('click', (e) => {
                    e.preventDefault();
                    if (restoreCopyBlocked()) return;
                    const rows = extractTableData(table);
                    const csv = formatTableAsCsv(rows);
                    downloadCsv(csv, 'mcp-ai-table-export.csv');
                });
            });

            thread.querySelectorAll('[data-disambiguate]').forEach((btn) => {
                btn.addEventListener('click', () => {
                    const query = btn.getAttribute('data-disambiguate');
                    if (query) {
                        handlers.onDisambiguate?.(query);
                    }
                });
            });
        }

        return {
            getAssistantArticle,
            updatePhaseTimeline,
            updateThinkingLoader,
            updateStreamingStatusUI,
            renderPhaseTimeline,
            renderAssistantBody,
            updateAssistantBubble,
            renderUserMessageHtml,
            renderStreamingAssistantHtml,
            appendTurnPair,
            rebindStreamingBodyEls,
            renderProse,
            renderAnswerContent,
            renderRecordLinks,
            renderCitedSources,
            thumbAttrs,
            renderMessagesHtml,
            bindThreadInteractions,
            documentEvidence,
            safeUiLinks,
            linkifyUiLinks,
        };
    }

    function makeRevealer(assistant, onPaint, { revealDivisor = 12, maxLagChars = 400 } = {}) {
        let pending = '';
        let raf = null;
        const reduced = prefersReducedMotion();

        function step() {
            raf = null;
            const n = Math.max(2, Math.ceil(pending.length / revealDivisor));
            assistant.content += pending.slice(0, n);
            pending = pending.slice(n);
            onPaint();
            if (pending) raf = requestAnimationFrame(step);
        }

        return {
            push(chunk) {
                if (reduced) {
                    assistant.content += chunk;
                    onPaint();
                    return;
                }
                pending += chunk;
                if (pending.length > maxLagChars) {
                    assistant.content += pending;
                    pending = '';
                    onPaint();
                    return;
                }
                if (!raf) raf = requestAnimationFrame(step);
            },
            flush() {
                if (raf) {
                    cancelAnimationFrame(raf);
                    raf = null;
                }
                if (pending) {
                    assistant.content += pending;
                    pending = '';
                    onPaint();
                }
            },
        };
    }

    window.McpAskAiStreamUi = {
        PHASE_GROUPS,
        CONTEXT_LABELS,
        CONTEXT_SUBJECTS,
        FULL_LOADER_SRC,
        prefersReducedMotion,
        deepCloneFreeze,
        contextLabel,
        contextSubject,
        makeRetryDescriptor,
        businessQueryEnvelope,
        recordDetailEvidence,
        loaderStageForMsg,
        loaderSrcForMsg,
        displayLabelForMsg,
        renderThinkingContent,
        mapStreamError,
        unavailableStateHtml,
        emptyStateHtml,
        createUi,
        makeRevealer,
        mendMarkdown,
    };
})();
