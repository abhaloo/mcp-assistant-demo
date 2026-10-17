<?php

namespace Tests;

use Illuminate\Contracts\Console\Kernel;
use Illuminate\Database\Events\MigrationsEnded;
use Illuminate\Foundation\Application;
use Illuminate\Support\Facades\Event;
use RuntimeException;

trait CreatesApplication
{
    /**
     * Creates the application.
     *
     * @return Application
     */
    public function createApplication()
    {
        $app = require __DIR__.'/../bootstrap/app.php';

        $app->make(Kernel::class)->bootstrap();

        $this->configureSqliteTestingDatabase($app);
        $this->keepAiProjectionViewsInSync($app);

        return $app;
    }

    /**
     * Task B5 / R1: keep the code-versioned ai_v1_* projection views present on
     * whichever DB the suite is using. MigrationsEnded fires at the end of the
     * migrate:fresh call RefreshDatabase::migrateDatabases() runs — which itself
     * only runs ONCE per process (guarded by RefreshDatabaseState::$migrated) and
     * strictly BEFORE RefreshDatabase::beginDatabaseTransaction() wraps a test in
     * a transaction. Applying the views there means the DDL never runs inside a
     * per-test transaction (which would implicitly commit on MySQL, breaking that
     * test's rollback), and it fires regardless of which test class happens to
     * trigger the one real migration this process ever does — no reliance on
     * suite/class execution order. migrate:fresh does not drop views by default
     * (--drop-views is opt-in), so the views survive every later test's refresh.
     * Re-applying is harmless: DROP VIEW IF EXISTS + CREATE VIEW is idempotent.
     */
    protected function keepAiProjectionViewsInSync($app): void
    {
        Event::listen(MigrationsEnded::class, static function () use ($app): void {
            $app->make(Kernel::class)->call('ai-projections:apply', [
                'connection' => $app['config']->get('database.default'),
            ]);
        });
    }

    /**
     * Point SQLite tests at an absolute file under database/ so RefreshDatabase can
     * migrate once + wrap each test in a transaction (Laravel 9 :memory: remigrates every test).
     *
     * Laravel ParallelTesting rewrites the configured path to `{path}_test_{token}`
     * (e.g. database/testing.sqlite_test_1) for RefreshDatabase cases.
     */
    protected function configureSqliteTestingDatabase($app): void
    {
        if ($app['config']->get('database.default') !== 'sqlite') {
            return;
        }

        $database = $app['config']->get('database.connections.sqlite.database');

        if (! is_string($database) || $database === ':memory:') {
            return;
        }

        $database = $this->resolveSqliteDatabasePath($database);
        $app['config']->set('database.connections.sqlite.database', $database);

        $this->ensureSqliteDatabaseFileExists($database);
    }

    protected function resolveSqliteDatabasePath(string $database): string
    {
        $normalized = str_replace('\\', '/', $database);

        // Relative phpunit.xml paths (e.g. database/testing.sqlite) → absolute under database/.
        if (! $this->isAbsoluteFilesystemPath($normalized)) {
            $database = database_path(basename($normalized));
        }

        $databaseRoot = realpath(database_path()) ?: database_path();
        $parent = dirname($database);
        $resolvedParent = realpath($parent) ?: $parent;

        $rootPrefix = rtrim(str_replace('\\', '/', $databaseRoot), '/').'/';
        $parentNorm = rtrim(str_replace('\\', '/', $resolvedParent), '/').'/';

        if ($parentNorm !== $rootPrefix && ! str_starts_with($parentNorm, $rootPrefix)) {
            throw new RuntimeException(
                "SQLite test database must live under database/: {$database}"
            );
        }

        return $database;
    }

    protected function ensureSqliteDatabaseFileExists(string $database): void
    {
        if (is_link($database)) {
            throw new RuntimeException("SQLite test database path must not be a symlink: {$database}");
        }

        if (file_exists($database)) {
            return;
        }

        $dir = dirname($database);
        if (! is_dir($dir) && ! @mkdir($dir, 0755, true) && ! is_dir($dir)) {
            throw new RuntimeException("Failed to create SQLite directory: {$dir}");
        }

        if (! @touch($database) && ! file_exists($database)) {
            throw new RuntimeException("Failed to create SQLite database file: {$database}");
        }
    }

    protected function isAbsoluteFilesystemPath(string $path): bool
    {
        return str_starts_with($path, '/')
            || preg_match('#^[A-Za-z]:[\\\\/]#', $path) === 1;
    }
}
