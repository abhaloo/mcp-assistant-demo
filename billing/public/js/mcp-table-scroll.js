/**
 * Dual horizontal scrollbar for wide tables.
 *
 * Adds a synced scrollbar above a table container so the user can scroll
 * horizontally from the top as well as via the native bottom scrollbar.
 * Opt in with:
 *
 *   <div data-dual-scroll>
 *     <div class="mcp-table-scroll-top" data-dual-scroll-top aria-hidden="true">
 *       <div class="mcp-table-scroll-top__rail"></div>
 *     </div>
 *     <div class="mcp-table-wrap" data-dual-scroll-wrap> ... <table> ... </div>
 *   </div>
 *
 * Re-measures on resize and after every Livewire DOM update (density toggle,
 * sort, search, pagination all change the table width).
 */
(function () {
    'use strict';

    function setup(root) {
        var top = root.querySelector('[data-dual-scroll-top]');
        var wrap = root.querySelector('[data-dual-scroll-wrap]');
        var rail = top && top.querySelector('.mcp-table-scroll-top__rail');

        function measure() {
            var table = wrap && wrap.querySelector('table');
            if (!table || !rail) return;
            rail.style.width = table.scrollWidth + 'px';
            top.style.display = (table.scrollWidth > wrap.clientWidth + 1) ? '' : 'none';
        }

        if (!top || !wrap || !rail) return;

        if (!root.__dualScroll) {
            var lock = false;
            top.addEventListener('scroll', function () {
                if (lock) return;
                lock = true;
                wrap.scrollLeft = top.scrollLeft;
                lock = false;
            });
            wrap.addEventListener('scroll', function () {
                if (lock) return;
                lock = true;
                top.scrollLeft = wrap.scrollLeft;
                lock = false;
            });
            window.addEventListener('resize', measure);
            if (window.ResizeObserver) {
                new ResizeObserver(measure).observe(wrap);
            }
            root.__dualScroll = true;
        }

        measure();
    }

    function init() {
        document.querySelectorAll('[data-dual-scroll]').forEach(setup);
    }

    document.addEventListener('DOMContentLoaded', init);
    document.addEventListener('livewire:init', function () {
        init();
        if (window.Livewire && typeof window.Livewire.hook === 'function') {
            window.Livewire.hook('commit', ({ succeed }) => {
                succeed(() => init());
            });
        }
    });
})();
