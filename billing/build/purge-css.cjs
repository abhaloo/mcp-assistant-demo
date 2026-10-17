/*
 * Static CSS purge — shrinks the two large vendored stylesheets
 * (coreui.min.css, simple.css) by removing rules whose selectors never appear
 * in the app's templates/JS. Originals are kept untouched; purged copies are
 * written alongside them as *.purged.css and referenced from the layout.
 *
 * Run:  npm run purge:css   (also runs automatically after `npm run build`)
 *
 * Safety model — three layers so dynamic/JS-added classes are never dropped:
 *   1. content scan  — every blade/php/js file is scanned for literal classes.
 *   2. colour families — Bootstrap contextual colours (btn-outline-danger, etc.)
 *      are kept whole because Blade builds them by string concatenation
 *      (e.g. `btn-outline-{{ $x ? 'light' : 'primary' }}`), so the literal class
 *      never appears for the scanner to find.
 *   3. JS-runtime classes — modal/dropdown/tooltip/tab/collapse state classes
 *      are added by CoreUI's JS at runtime and live in no template.
 *
 * PurgeCSS keeps all @keyframes, @font-face and CSS custom properties by
 * default (keyframes/fontFace/variables = false), so theming tokens are safe.
 */
const fs = require('fs');
const path = require('path');
const { PurgeCSS } = require('purgecss');

const ROOT = path.resolve(__dirname, '..');

// ── Where used classes can appear ────────────────────────────────────────────
const content = [
    'resources/views/**/*.blade.php',
    'resources/views/**/*.php',
    'app/**/*.php',           // Livewire components / helpers that emit class strings
    'public/js/**/*.js',      // mcp-sidebar.js, select2.js, coreui bundle, etc.
    'resources/js/**/*.js',
].map((g) => path.join(ROOT, g).replace(/\\/g, '/'));

// ── Files to purge (original -> purged copy) ─────────────────────────────────
const targets = [
    { src: 'public/css/coreui.min.css', out: 'public/css/coreui.purged.css' },
    { src: 'public/css/simple.css', out: 'public/css/simple.purged.css' },
];

// ── Layer 2: Bootstrap contextual colour families (built dynamically) ────────
const COLORS = [
    'primary', 'secondary', 'success', 'danger', 'warning', 'info',
    'light', 'dark', 'white', 'black', 'body', 'muted',
];
const colorSafelist = [];
for (const c of COLORS) {
    colorSafelist.push(
        new RegExp(`^text-${c}(-\\d+)?$`),
        new RegExp(`^bg-${c}$`),
        new RegExp(`^text-bg-${c}$`),
        new RegExp(`^border-${c}$`),
        new RegExp(`^btn-${c}$`),
        new RegExp(`^btn-outline-${c}$`),
        new RegExp(`^alert-${c}$`),
        new RegExp(`^list-group-item-${c}$`),
        new RegExp(`^table-${c}$`),
        new RegExp(`^link-${c}$`),
    );
}

// ── Layer 3: classes added by JS at runtime (never literal in templates) ─────
const safelist = {
    standard: [
        'html', 'body', 'active', 'show', 'showing', 'hide', 'hiding', 'fade',
        'collapse', 'collapsing', 'collapsed', 'disabled', 'open',
        'modal-open', 'modal-static', 'modal-backdrop', 'offcanvas-backdrop',
        'fade', 'was-validated', 'is-valid', 'is-invalid',
        'visually-hidden', 'visually-hidden-focusable',
        'bg-gradient', 'text-white-50', 'text-black-50',
    ],
    deep: [
        // whole component sub-trees toggled/created by CoreUI/Bootstrap JS
        /^modal/, /^offcanvas/, /^dropdown/, /^tooltip/, /^bs-tooltip/,
        /^popover/, /^bs-popover/, /^carousel/, /^accordion/, /^toast/,
        /^spinner/, /^placeholder/, /^progress-bar/, /^collaps/, /^fade/,
        /show$/, /active$/, /disabled$/,
    ],
    greedy: [
        // colour families + pagination/validation built dynamically or by JS
        ...colorSafelist,
        /^bg-opacity-/, /^text-opacity-/, /^bg-\w+-subtle$/, /^border-opacity-/,
        /^pagination/, /^page-/, /^bs-/, /^cui-/,
    ],
};

(async () => {
    let totalBefore = 0;
    let totalAfter = 0;
    for (const t of targets) {
        const srcPath = path.join(ROOT, t.src);
        const outPath = path.join(ROOT, t.out);
        if (!fs.existsSync(srcPath)) {
            console.warn(`  skip (missing): ${t.src}`);
            continue;
        }
        const before = fs.statSync(srcPath).size;
        const results = await new PurgeCSS().purge({
            content,
            css: [srcPath.replace(/\\/g, '/')],
            safelist,
            // defaults keep keyframes/fontFace/variables — leave them on
        });
        const result = results[0];
        if (!result) {
            throw new Error(`PurgeCSS returned no result for ${t.src} (check content globs matched files)`);
        }
        fs.writeFileSync(outPath, result.css);
        const after = Buffer.byteLength(result.css);
        totalBefore += before;
        totalAfter += after;
        const pct = ((1 - after / before) * 100).toFixed(1);
        console.log(
            `  ${t.src.padEnd(28)} ${(before / 1024).toFixed(0).padStart(4)}KB -> ` +
            `${(after / 1024).toFixed(0).padStart(4)}KB  (-${pct}%)  ->  ${t.out}`
        );
    }
    const pct = ((1 - totalAfter / totalBefore) * 100).toFixed(1);
    console.log(
        `  TOTAL ${(totalBefore / 1024).toFixed(0)}KB -> ${(totalAfter / 1024).toFixed(0)}KB  (-${pct}%)`
    );
})().catch((e) => {
    console.error('purge-css failed:', e);
    process.exit(1);
});
