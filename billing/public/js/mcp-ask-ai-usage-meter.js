/*
 * MCP Ask AI — usage meter.
 *
 * A line under the composer showing how much of the person's own monthly
 * Ask AI allowance is used. The first paint comes from the percentage the panel
 * carried on load; after each turn the panel asks for the current figure once
 * the stream has closed. The relay charges a turn just after it sends the last
 * frame, so a read that lands too early is repeated once.
 */
(function () {
    'use strict';

    const NEAR_PCT = 80;
    const SETTLE_MS = 1200;

    let node = null;
    let creditsUrl = '';
    let lastPct = null;
    let generation = 0;

    function resetDay(iso) {
        const at = new Date(`${iso}T00:00:00Z`);
        if (Number.isNaN(at.getTime())) return null;
        return `${at.getUTCDate()} ${at.toLocaleString('en-GB', { month: 'short', timeZone: 'UTC' })}`;
    }

    function build() {
        node.replaceChildren();
        node.removeAttribute('role');
        node.setAttribute('aria-live', 'polite');
        const label = document.createElement('span');
        label.className = 'mcp-ask-ai__usage-label';
        node.append(label);
    }

    function paint(credits) {
        if (!node || !credits) return;
        const raw = Number(credits.pct);
        if (!Number.isFinite(raw)) return;
        if (!node.querySelector('.mcp-ask-ai__usage-label')) build();

        const pct = Math.min(100, Math.max(0, raw));
        lastPct = pct;
        node.classList.toggle('is-near', pct >= NEAR_PCT && pct < 100);
        node.classList.toggle('is-full', pct >= 100);

        const text = `${Math.round(pct)}% of monthly use`;
        const reset = credits.reset_date ? resetDay(credits.reset_date) : null;
        node.querySelector('.mcp-ask-ai__usage-label').textContent = text;
        node.setAttribute('aria-label', text);
        if (reset) node.title = `Resets ${reset}`;
        else node.removeAttribute('title');
        node.hidden = false;
    }

    async function read() {
        const response = await window.fetch(creditsUrl, {
            headers: { Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
            credentials: 'same-origin',
        });
        if (!response.ok) throw new Error(`credits read returned ${response.status}`);
        return response.json();
    }

    /**
     * Reads the current figure. After a completed turn the figure must have
     * moved; when it has not, the charge has not landed yet and one more read
     * follows shortly. A newer refresh supersedes an older one still in flight,
     * so a slow read never paints over a fresher figure. A failed read keeps
     * the last paint.
     */
    async function refresh({ afterTurn = false } = {}) {
        if (!creditsUrl) return;
        const mine = ++generation;
        const before = lastPct;
        try {
            const first = await read();
            if (mine !== generation) return;
            paint(first);
            if (afterTurn && before !== null && lastPct <= before) {
                await new Promise((resolve) => setTimeout(resolve, SETTLE_MS));
                const second = await read();
                if (mine !== generation) return;
                paint(second);
            }
        } catch (err) {
            console.warn('[ask-ai] usage meter kept its last figure:', err);
        }
    }

    function init(root) {
        node = root?.querySelector('[data-mcp-ask-usage]') || null;
        creditsUrl = root?.dataset.creditsUrl || '';
        if (!node) return;
        try {
            paint(JSON.parse(root.dataset.credits || 'null'));
        } catch (err) {
            node.hidden = true;
            console.warn('[ask-ai] usage meter could not read the panel figure:', err);
        }
    }

    window.McpAskAiUsageMeter = { init, paint, refresh };
})();
