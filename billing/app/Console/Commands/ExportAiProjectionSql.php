<?php

declare(strict_types=1);

namespace App\Console\Commands;

use App\Authorization\BusinessRecordRegistry;
use App\Authorization\Projections\ProjectionDefinitions;
use Illuminate\Console\Command;

/**
 * Task B2 fix round 1 / I4 (Important, reviewer finding; controller ruling:
 * IMPLEMENT, not defer — plan §3.7 mandates it) — plan §3.7 requires two
 * things this task's first submission had not met: "Record the generated
 * definition hash" and "keep the previous generated SQL artifact so
 * rollback can recreate the prior definition deterministically." Before
 * this fix, `projection_definition_sha256` in the acceptance ledger hashed
 * the PHP SOURCE FILE (app/Authorization/Projections/ProjectionDefinitions.php)
 * — a proxy for "the code that generates the SQL", not the generated SQL
 * itself — and no generated SQL was ever persisted anywhere a rollback
 * could recover it from.
 *
 * Writes the deterministic, code-derived MySQL DDL every `ai_v1_*` view
 * would apply as (`ProjectionViewDefinition::createOrReplaceStatement()` —
 * the SAME atomic statement `ApplyAiProjectionViews::apply()` runs against
 * a real MySQL connection) to ONE committed file,
 * `database/ai-projections/generated-mysql.sql`, in deterministic
 * (alphabetical resourceType) order. The mutable current artifact is paired
 * with immutable historical snapshots in `database/ai-projections/history/`.
 * The first snapshot reconstructs source commit `4cefcce` and is stored as
 * `history/4cefcce-mysql.sql` (SHA-256
 * `383c3d6096393567018ede468e15fdfba5105bfeed66d2395231851bb488e93a`).
 * The acceptance ledger hashes the generated artifacts, not PHP source files.
 *
 * MySQL only (the production driver) — this artifact exists to describe
 * what ships to production, not every driver `ProjectionDefinitions` can
 * build for.
 */
final class ExportAiProjectionSql extends Command
{
    protected $signature = 'ai-projections:export-sql';

    protected $description = 'Write the deterministic, generated MySQL ai_v1_* view DDL to database/ai-projections/generated-mysql.sql';

    private const string ARTIFACT_RELATIVE_PATH = 'database/ai-projections/generated-mysql.sql';

    public function __construct(private readonly BusinessRecordRegistry $registry = new BusinessRecordRegistry)
    {
        parent::__construct();
    }

    public function handle(): int
    {
        $sql = self::generate($this->registry);
        $path = base_path(self::ARTIFACT_RELATIVE_PATH);
        $dir = dirname($path);

        // Race-safe create: another process may have created the dir between
        // the is_dir() check and mkdir() -- only a genuine failure (still no
        // dir afterward) is fatal (mirrors ExportAiPolicyManifest's own
        // bundles-dir creation discipline).
        if (! is_dir($dir) && ! mkdir($dir, 0755, true) && ! is_dir($dir)) {
            $this->components->error("Could not create directory [{$dir}].");

            return self::FAILURE;
        }

        if (file_put_contents($path, $sql) === false) {
            $this->components->error('Could not write '.self::ARTIFACT_RELATIVE_PATH.'.');

            return self::FAILURE;
        }

        $this->components->twoColumnDetail(self::ARTIFACT_RELATIVE_PATH, '<fg=green>written</>');
        $this->components->info('sha256: '.hash('sha256', $sql));

        return self::SUCCESS;
    }

    /**
     * Pure, deterministic generation — no DB, no clock (mirrors
     * PolicyManifestExporter's own "no DB, no ambient reads" determinism
     * discipline). Exposed as a public static so both the console command
     * AND the committed-artifact drift test can call it directly without
     * running Artisan (the same relationship PolicyManifestExporterTest's
     * drift gate has with PolicyManifestExporter::canonicalJson() — the
     * console command is a thin writer, not the source of truth).
     */
    public static function generate(BusinessRecordRegistry $registry = new BusinessRecordRegistry): string
    {
        $definitions = (new ProjectionDefinitions($registry, driver: 'mysql'))->all();
        $resourceTypes = array_keys($definitions);
        sort($resourceTypes);

        $lines = [
            '-- GENERATED FILE -- do not edit by hand.',
            '-- Regenerate with: php artisan ai-projections:export-sql',
            '-- Source: app/Authorization/Projections/ProjectionDefinitions.php (driver: mysql)',
            '-- Task B2 fix round 1 / I4 -- plan section 3.7: "Record the generated definition',
            '-- hash" + "keep the previous generated SQL artifact so rollback can recreate the',
            '-- prior definition deterministically". The pre-B2 definition is preserved at',
            '-- database/ai-projections/history/4cefcce-mysql.sql (source commit 4cefcce;',
            '-- sha256:383c3d6096393567018ede468e15fdfba5105bfeed66d2395231851bb488e93a).',
            '',
        ];

        foreach ($resourceTypes as $resourceType) {
            $definition = $definitions[$resourceType];
            $lines[] = "-- {$resourceType} -> {$definition->viewName}";
            $lines[] = $definition->createOrReplaceStatement().';';
            $lines[] = '';
        }

        return implode("\n", $lines);
    }
}
