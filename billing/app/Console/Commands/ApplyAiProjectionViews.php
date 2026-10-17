<?php

declare(strict_types=1);

namespace App\Console\Commands;

use App\Authorization\BusinessRecordRegistry;
use App\Authorization\Projections\ProjectionDefinitions;
use App\Authorization\Projections\ProjectionViewDefinition;
use Illuminate\Console\Command;
use Illuminate\Database\Connection;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;
use Throwable;

/**
 * Task B5 / R3 — idempotently create-or-replace the code-versioned `ai_v1_*`
 * projection views on an EXPLICIT connection. No migration calls this; no
 * production code path calls this either (feature-off, per the task brief).
 *
 * The connection is a REQUIRED argument with no default — this command must
 * never fall back onto `config('database.default')` (the app DB) by
 * ambient accident. `--check` reports column-shape drift between the live
 * views and the code definitions without modifying anything.
 *
 * Task B2 / §3.7 — `ProjectionDefinitions` is now built FRESH per call, here,
 * from the TARGET connection's own real driver
 * (`DB::connection($connectionName)->getDriverName()`) — never from an
 * injected/default instance. Building it up front in the constructor (as
 * this class used to) would have hardcoded ProjectionDefinitions' own
 * driver default (now 'mysql') regardless of which connection `handle()` is
 * actually asked to target; the exact regression this hit while implementing
 * this task, empirically confirmed: applied against the real sqlite test
 * connection, that emitted MySQL-only `CAST(... AS CHAR CHARACTER SET
 * utf8mb4) COLLATE utf8mb4_unicode_ci` SQL sqlite's CAST grammar cannot even
 * parse — breaking every projection test in the suite via
 * tests/CreatesApplication.php's MigrationsEnded hook (see
 * ApplyAiProjectionViewsTest::apply_resolves_sql_from_the_connections_real_driver_not_a_hardcoded_default()).
 *
 * Idempotent by construction: on an engine without atomic `CREATE OR REPLACE
 * VIEW` support (SQLite), every view is DROP VIEW IF EXISTS'd then recreated
 * from the same deterministic definition; on MySQL, `CREATE OR REPLACE VIEW`
 * itself is idempotent (ProjectionViewDefinition::applyStatements()) — either
 * way, running the command twice in a row leaves the database in the same
 * state as running it once.
 */
final class ApplyAiProjectionViews extends Command
{
    protected $signature = 'ai-projections:apply
                            {connection : Explicit DB connection name to target -- no ambient default onto the app DB}
                            {--check : Report column-shape and collation drift between the live views and the code definitions, without modifying anything}';

    protected $description = 'Create or replace the versioned ai_v1_* projection views on an explicit connection (or --check for drift)';

    /** Task B2 / §3.7 — the collation every derived-text projection column must carry on MySQL. */
    private const string CANONICAL_COLLATION = 'utf8mb4_unicode_ci';

    public function __construct(private readonly BusinessRecordRegistry $registry = new BusinessRecordRegistry)
    {
        parent::__construct();
    }

    public function handle(): int
    {
        $connectionName = trim((string) $this->argument('connection'));

        if ($connectionName === '') {
            $this->components->error('A non-empty explicit connection name is required.');

            return self::FAILURE;
        }

        if (! is_array(config("database.connections.{$connectionName}"))) {
            $this->components->error("Unknown database connection [{$connectionName}].");

            return self::FAILURE;
        }

        return $this->option('check')
            ? $this->checkDrift($connectionName)
            : $this->apply($connectionName);
    }

    /** @return list<ProjectionViewDefinition> */
    private function definitionsFor(Connection $connection): array
    {
        return array_values((new ProjectionDefinitions($this->registry, driver: $connection->getDriverName()))->all());
    }

    private function apply(string $connectionName): int
    {
        $connection = DB::connection($connectionName);

        foreach ($this->definitionsFor($connection) as $definition) {
            foreach ($definition->applyStatements() as $statement) {
                $connection->statement($statement);
            }
            $this->components->twoColumnDetail($definition->viewName, "<fg=green>applied</> ({$definition->resourceType})");
        }

        return self::SUCCESS;
    }

    private function checkDrift(string $connectionName): int
    {
        $connection = DB::connection($connectionName);
        $drifted = [];
        $liveViewNames = collect(Schema::connection($connectionName)->getViews())
            ->pluck('name')
            ->all();

        foreach ($this->definitionsFor($connection) as $definition) {
            $liveColumns = $this->liveColumns($connectionName, $definition->viewName, $liveViewNames);

            if ($liveColumns === null) {
                $drifted[] = $definition->viewName;
                $this->components->twoColumnDetail($definition->viewName, '<fg=red>missing</>');

                continue;
            }

            sort($liveColumns);
            $expectedColumns = $definition->columns;
            sort($expectedColumns);

            if ($liveColumns !== $expectedColumns) {
                $drifted[] = $definition->viewName;
                $this->components->twoColumnDetail($definition->viewName, '<fg=red>column drift</>');

                continue;
            }

            if (! $this->derivedTextIsHealthy($connection, $definition)) {
                $drifted[] = $definition->viewName;
                $this->components->twoColumnDetail($definition->viewName, '<fg=red>collation drift</>');

                continue;
            }

            $this->components->twoColumnDetail($definition->viewName, '<fg=green>in sync</>');
        }

        if ($drifted !== []) {
            $this->components->error(
                'Drift detected in: '.implode(', ', $drifted).'. Run ai-projections:apply to reconcile.'
            );

            return self::FAILURE;
        }

        $this->components->info('No drift: every ai_v1_* view matches its code definition.');

        return self::SUCCESS;
    }

    /**
     * @param  list<string>  $liveViewNames
     * @return list<string>|null null when the view does not exist.
     *
     * Schema::hasTable() is deliberately NOT used here: both the MySQL grammar
     * (`table_type in ('BASE TABLE', 'SYSTEM VERSIONED')`) and the SQLite
     * grammar (`type = 'table'`) exclude views by construction, so it always
     * reports a view as missing. getViews() is the portable existence check.
     *
     * No try/catch here: a genuinely broken connection must surface as a loud
     * command failure, not be silently reinterpreted as "every view is
     * missing" — swallowing every Throwable would hide that distinction from
     * whoever is reading --check's output.
     */
    private function liveColumns(string $connectionName, string $viewName, array $liveViewNames): ?array
    {
        if (! in_array($viewName, $liveViewNames, true)) {
            return null;
        }

        return Schema::connection($connectionName)->getColumnListing($viewName);
    }

    /**
     * Task B2 / §3.7 — `--check` extended beyond column NAMES: a derived-text
     * column can be present (satisfying the column-listing check above) while
     * carrying the wrong collation or being altogether unqueryable. A view
     * with no derived-text columns has nothing to check here and is healthy
     * by definition.
     */
    private function derivedTextIsHealthy(Connection $connection, ProjectionViewDefinition $definition): bool
    {
        if ($definition->derivedTextColumns === []) {
            return true;
        }

        return $connection->getDriverName() === 'mysql'
            ? $this->collationMatchesOnMysql($connection, $definition)
            : $this->likeSmoke($connection, $definition);
    }

    /**
     * Task B2 / §3.7 — the real collation proof: every derived-text column's
     * `information_schema.COLUMNS.COLLATION_NAME` must equal the canonical
     * `utf8mb4_unicode_ci`. Only ever runs on a MySQL connection —
     * information_schema.COLUMNS does not exist on SQLite, and this task's
     * own empirical proof (see this class's docblock) shows there is no
     * meaningful "expected collation" to assert on SQLite in the first
     * place. This is the code path P1/CI's real MySQL connection exercises;
     * no local/CI-today automated test constructs a MySQL connection to
     * exercise it directly (documented proof limit, this task's ledger).
     */
    private function collationMatchesOnMysql(Connection $connection, ProjectionViewDefinition $definition): bool
    {
        $placeholders = implode(',', array_fill(0, count($definition->derivedTextColumns), '?'));

        $rows = $connection->select(
            'SELECT COLUMN_NAME, COLLATION_NAME FROM information_schema.COLUMNS '
            .'WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ? AND COLUMN_NAME IN ('.$placeholders.')',
            [$definition->viewName, ...$definition->derivedTextColumns],
        );

        if (count($rows) !== count($definition->derivedTextColumns)) {
            return false;
        }

        foreach ($rows as $row) {
            if ($row->COLLATION_NAME !== self::CANONICAL_COLLATION) {
                return false;
            }
        }

        return true;
    }

    /**
     * Task B2 / §3.7 — a bounded LIKE smoke query on every derived-text
     * column, for connections where the real collation cannot be asserted
     * (SQLite). NOT collation evidence — it proves the column stays
     * queryable, catching exactly the failure mode this task's own empirical
     * proof found (a literal, unrecognized COLLATE clause makes SQLite
     * reject every query against the view, even one that never touches the
     * collated column) without asserting anything about actual collation
     * behavior.
     */
    private function likeSmoke(Connection $connection, ProjectionViewDefinition $definition): bool
    {
        foreach ($definition->derivedTextColumns as $column) {
            try {
                $connection->table($definition->viewName)
                    ->where($column, 'like', '%')
                    ->limit(1)
                    ->get();
            } catch (Throwable) {
                return false;
            }
        }

        return true;
    }
}
