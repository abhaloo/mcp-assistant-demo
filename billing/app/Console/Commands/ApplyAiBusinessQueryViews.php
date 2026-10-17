<?php

declare(strict_types=1);

namespace App\Console\Commands;

use App\Authorization\BusinessQuery\SemanticViews;
use Illuminate\Console\Command;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

/**
 * Idempotently create-or-replace the dedicated ai_v1_bq_* semantic fact views
 * on an EXPLICIT connection. Mirrors ApplyAiProjectionViews: no ambient default
 * onto the app DB; --check reports column-shape drift without modifying anything.
 *
 * SemanticViews is built fresh per call from the target connection's real driver
 * so SQLite never receives MySQL-only CREATE OR REPLACE / collation SQL.
 */
final class ApplyAiBusinessQueryViews extends Command
{
    protected $signature = 'ai-business-query:apply
                            {connection : Explicit DB connection name to target -- no ambient default onto the app DB}
                            {--check : Report column-shape drift between the live views and the code definitions, without modifying anything}';

    protected $description = 'Create or replace the versioned ai_v1_bq_* semantic fact views on an explicit connection (or --check for drift)';

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

    private function apply(string $connectionName): int
    {
        $connection = DB::connection($connectionName);
        $views = new SemanticViews($connection->getDriverName());

        foreach (array_keys(SemanticViews::COLUMNS) as $viewName) {
            foreach ($views->applyStatements($viewName) as $statement) {
                $connection->statement($statement);
            }
            $this->components->twoColumnDetail($viewName, '<fg=green>applied</>');
        }

        return self::SUCCESS;
    }

    private function checkDrift(string $connectionName): int
    {
        $connection = DB::connection($connectionName);
        $views = new SemanticViews($connection->getDriverName());
        $drifted = [];
        $liveViewNames = collect(Schema::connection($connectionName)->getViews())
            ->pluck('name')
            ->all();

        foreach (array_keys(SemanticViews::COLUMNS) as $viewName) {
            $liveColumns = $this->liveColumns($connectionName, $viewName, $liveViewNames);

            if ($liveColumns === null) {
                $drifted[] = $viewName;
                $this->components->twoColumnDetail($viewName, '<fg=red>missing</>');

                continue;
            }

            $expected = $views->columnsFor($viewName);
            if ($liveColumns !== $expected) {
                $drifted[] = $viewName;
                $this->components->twoColumnDetail($viewName, '<fg=red>column drift</>');

                continue;
            }

            $this->components->twoColumnDetail($viewName, '<fg=green>in sync</>');
        }

        if ($drifted !== []) {
            $this->components->error(
                'Drift detected in: '.implode(', ', $drifted).'. Run ai-business-query:apply to reconcile.'
            );

            return self::FAILURE;
        }

        $this->components->info('No drift: every ai_v1_bq_* view matches its code definition.');

        return self::SUCCESS;
    }

    /**
     * @param  list<string>  $liveViewNames
     * @return list<string>|null
     */
    private function liveColumns(string $connectionName, string $viewName, array $liveViewNames): ?array
    {
        if (! in_array($viewName, $liveViewNames, true)) {
            return null;
        }

        return Schema::connection($connectionName)->getColumnListing($viewName);
    }
}
