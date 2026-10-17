import { defineConfig } from 'vite';
import laravel from 'laravel-vite-plugin';

export default defineConfig({
    // Worktrees share node_modules via a junction, so the default cacheDir
    // (node_modules/.vite) would be shared too - racing the dep-optimizer across
    // parallel dev servers. Keep the cache worktree-local.
    cacheDir: '.vite',
    plugins: [
        laravel({
            input: [
                'resources/sass/app.scss',
                'resources/js/app.js',
                'resources/js/ask_ai_v2/index.js',
                'resources/js/ask_ai_v2/restore_client.js',
            ],
            refresh: true,
        }),
    ],
});
