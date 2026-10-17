/*
 * Shared MCP dropdown chrome: open/close, exclusive menus, keyboard, outside click.
 * Surfaces fill the menu; this file does not know Ask vs Act vs All Records.
 */
(function () {
    'use strict';

    function dropdownOf(el) {
        return el?.closest('[data-mcp-dropdown]') || null;
    }

    function triggerOf(dropdown) {
        return dropdown.querySelector('[data-mcp-dropdown-trigger]');
    }

    function menuOf(dropdown) {
        return dropdown.querySelector('[data-mcp-dropdown-menu]');
    }

    function isOpen(dropdown) {
        const menu = menuOf(dropdown);
        return Boolean(menu && !menu.hidden);
    }

    function isShown(el) {
        return Boolean(el && !el.closest('[hidden]'));
    }

    function tabbables() {
        return [...document.querySelectorAll(
            'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        )].filter((el) => isShown(el) && el.tabIndex >= 0);
    }

    function close(dropdown, { refocus = true } = {}) {
        if (!dropdown) return;
        const menu = menuOf(dropdown);
        const trigger = triggerOf(dropdown);
        if (!menu || !trigger) return;
        menu.hidden = true;
        trigger.setAttribute('aria-expanded', 'false');
        if (refocus) trigger.focus();
    }

    function closeAll(except, opts) {
        document.querySelectorAll('[data-mcp-dropdown]').forEach((dropdown) => {
            if (dropdown !== except && isOpen(dropdown)) close(dropdown, opts || { refocus: false });
        });
    }

    function enabledItems(menu) {
        return [...menu.querySelectorAll('[role="menuitemradio"]:not([disabled]), [role="menuitem"]:not([disabled])')];
    }

    function open(dropdown, { focusLast = false } = {}) {
        if (!dropdown) return;
        const menu = menuOf(dropdown);
        const trigger = triggerOf(dropdown);
        if (!menu || !trigger) return;
        closeAll(dropdown, { refocus: false });
        dropdown.dispatchEvent(new CustomEvent('mcp-dropdown:beforeopen', { bubbles: true }));
        menu.hidden = false;
        trigger.setAttribute('aria-expanded', 'true');
        const list = enabledItems(menu);
        (focusLast ? list[list.length - 1] : list[0])?.focus();
    }

    function toggle(dropdown) {
        if (isOpen(dropdown)) close(dropdown);
        else open(dropdown);
    }

    function option({ text, checked = false, disabled = false, reason = null, icon = null, onChoose }) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'mcp-dropdown__option';
        button.setAttribute('role', 'menuitemradio');
        button.setAttribute('aria-checked', checked ? 'true' : 'false');
        if (disabled) {
            button.disabled = true;
            button.setAttribute('aria-disabled', 'true');
            if (reason) button.title = reason;
        }
        const mark = document.createElement('i');
        mark.className = 'fa ' + (icon || 'fa-check');
        mark.setAttribute('aria-hidden', 'true');
        if (!icon && !checked) mark.hidden = true;
        button.append(mark, document.createTextNode(' ' + text));
        button.addEventListener('click', () => {
            if (!button.disabled && typeof onChoose === 'function') onChoose();
        });
        return button;
    }

    function onMenuKeydown(event) {
        const dropdown = dropdownOf(event.currentTarget);
        if (!dropdown) return;
        const list = enabledItems(menuOf(dropdown));
        if (list.length === 0) return;
        const index = list.indexOf(document.activeElement);
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            close(dropdown);
        } else if (event.key === 'ArrowDown') {
            event.preventDefault();
            list[(index + 1) % list.length]?.focus();
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            list[(index - 1 + list.length) % list.length]?.focus();
        } else if (event.key === 'Home') {
            event.preventDefault();
            list[0]?.focus();
        } else if (event.key === 'End') {
            event.preventDefault();
            list[list.length - 1]?.focus();
        } else if (event.key === 'Tab') {
            event.preventDefault();
            const trigger = triggerOf(dropdown);
            close(dropdown, { refocus: false });
            const order = tabbables();
            const at = order.indexOf(trigger);
            const next = event.shiftKey ? order[at - 1] : order[at + 1];
            (next || trigger)?.focus();
        }
    }

    function bind(root) {
        const scope = root || document;
        scope.querySelectorAll('[data-mcp-dropdown]').forEach((dropdown) => {
            if (dropdown.dataset.mcpDropdownBound === '1') return;
            const trigger = triggerOf(dropdown);
            const menu = menuOf(dropdown);
            if (!trigger || !menu) return;
            dropdown.dataset.mcpDropdownBound = '1';
            trigger.addEventListener('click', () => toggle(dropdown));
            trigger.addEventListener('keydown', (event) => {
                if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                    event.preventDefault();
                    if (!isOpen(dropdown)) open(dropdown, { focusLast: event.key === 'ArrowUp' });
                } else if (event.key === 'Escape' && isOpen(dropdown)) {
                    event.preventDefault();
                    event.stopPropagation();
                    close(dropdown);
                }
            });
            menu.addEventListener('keydown', onMenuKeydown);
            menu.addEventListener('click', (event) => {
                const item = event.target.closest('[role="menuitemradio"], [role="menuitem"]');
                if (item && !item.disabled) close(dropdown);
            });
        });
        if (!window.__mcpDropdownDocBound) {
            window.__mcpDropdownDocBound = true;
            document.addEventListener('click', (event) => {
                document.querySelectorAll('[data-mcp-dropdown]').forEach((dropdown) => {
                    if (isOpen(dropdown) && !dropdown.contains(event.target)) {
                        const hadFocus = dropdown.contains(document.activeElement);
                        close(dropdown, { refocus: false });
                        if (hadFocus && (document.activeElement === document.body || dropdown.contains(document.activeElement))) {
                            if (event.target && event.target !== document.body && typeof event.target.focus === 'function') {
                                event.target.focus();
                            }
                        }
                    }
                });
            });
        }
    }

    function boot() {
        bind(document);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }

    window.McpDropdown = { bind, open, close, closeAll, toggle, option };
})();
