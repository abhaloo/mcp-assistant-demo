<?php

declare(strict_types=1);

namespace App\Console\Commands;

use App\Models\AiDetailMigrationLedger;
use App\Models\AiJobDetailValue;
use App\Models\WorkOrder;
use App\Services\AskAi\DetailAttributes\DefinitionWriter;
use App\Services\AskAi\DetailAttributes\TypedValueWriter;
use Illuminate\Console\Command;
use InvalidArgumentException;

class BackfillAiDetailAttributes extends Command
{
    /** @var array<string, string> */
    private const UNSUPPORTED_FAMILY_REASONS = [
        'inside_material' => 'no verified source column or approved business meaning',
        'profile' => 'no verified source column or approved business meaning',
    ];

    public static function unsupportedFamilyReason(string $family): string
    {
        return self::UNSUPPORTED_FAMILY_REASONS[$family]
            ?? 'family is not present in the approved producer capability set';
    }

    /**
     * The name and signature of the console command.
     *
     * @var string
     */
    protected $signature = 'ai:backfill-detail-attributes
                            {--family=print.sides : Attribute family to backfill}
                            {--job-id= : Specific job ID to target}
                            {--expected-fingerprint= : Expected fingerprint for concurrency guard}
                            {--force : Force overwrite even if unchanged}';

    /**
     * The console command description.
     *
     * @var string
     */
    protected $description = 'Offline backfill of versioned typed detail attributes with optimistic concurrency and audit ledger';

    /**
     * Execute the console command.
     */
    public function handle(): int
    {
        $family = (string) $this->option('family');
        $jobId = $this->option('job-id');
        $expectedFingerprint = $this->option('expected-fingerprint');
        $force = (bool) $this->option('force');

        if ($family !== 'print.sides' && $family !== 'ordered_qty') {
            $reason = self::unsupportedFamilyReason($family);
            $this->error("Unsupported attribute family: {$family} ({$reason})");

            return 1;
        }

        $query = WorkOrder::query();
        if ($jobId) {
            $query->where('id', (int) $jobId);
        }

        $jobs = $query->get();
        $this->info("Found {$jobs->count()} jobs to inspect for family [{$family}]");

        foreach ($jobs as $job) {
            if ($job->entity_id === null) {
                throw new InvalidArgumentException("Job {$job->id} entity_id is required for detail backfill.");
            }

            if ($family === 'print.sides') {
                $this->backfillJobPrintSides($job, $expectedFingerprint, $force);
            } elseif ($family === 'ordered_qty') {
                $this->backfillJobOrderedQuantity($job, $expectedFingerprint, $force);
            }
        }

        $this->info("Backfill complete for family [{$family}]");

        return 0;
    }

    public static function orderedQuantityDisplayValue(mixed $value): ?string
    {
        if (! is_scalar($value)) {
            return null;
        }

        $display = trim((string) $value);

        return $display === '' ? null : $display;
    }

    protected function backfillJobOrderedQuantity(WorkOrder $job, ?string $expectedFingerprint, bool $force): void
    {
        $rawString = self::orderedQuantityDisplayValue($job->ordered_qty);
        if ($rawString === null) {
            return;
        }

        $sourceFingerprint = hash('sha256', $rawString);

        if ($expectedFingerprint !== null && $expectedFingerprint !== $sourceFingerprint) {
            AiDetailMigrationLedger::create([
                'entity_id' => $job->entity_id,
                'resource_type' => 'job',
                'resource_id' => $job->id,
                'attribute_key' => 'ordered_qty',
                'source_raw_value' => $rawString,
                'source_fingerprint' => $sourceFingerprint,
                'status' => 'conflict',
                'reason' => 'fingerprint_mismatch',
                'conflict_details' => [
                    'expected' => $expectedFingerprint,
                    'actual' => $sourceFingerprint,
                ],
            ]);

            return;
        }

        $family = (new DefinitionWriter)->findOrCreateFamily('ordered_qty', [
            'label' => 'Ordered quantity (raw)',
            'description' => 'Authorized raw work-order quantity. Numeric comparison is not supported until a canonical unit contract exists.',
            'resource_type' => 'job',
            'value_kind' => 'string',
            'entity_id' => $job->entity_id,
        ]);
        $revision = (new DefinitionWriter)->registerRevision($family, 'ordered_qty', 1, [
            'data_type' => 'string',
            'value_kind' => 'string',
            'metadata' => [
                'source_table' => 'work_orders',
                'source_column' => 'ordered_qty',
                'interpretation' => 'raw_display_only',
                'numeric_comparison' => 'unsupported_without_canonical_unit',
            ],
        ]);
        $existing = AiJobDetailValue::where('work_order_id', $job->id)
            ->where('definition_revision_id', $revision->id)
            ->first();

        if ($existing && ! $force) {
            if ($existing->source_updated_at && $job->updated_at && $existing->source_updated_at > $job->updated_at) {
                AiDetailMigrationLedger::create([
                    'entity_id' => $job->entity_id,
                    'resource_type' => 'job',
                    'resource_id' => $job->id,
                    'attribute_key' => 'ordered_qty',
                    'source_raw_value' => $rawString,
                    'source_fingerprint' => $sourceFingerprint,
                    'status' => 'stale_skipped',
                    'reason' => 'stale_source',
                ]);

                return;
            }

            if ($existing->source_fingerprint === $sourceFingerprint) {
                return;
            }
        }

        $value = (new TypedValueWriter)->write(
            $job,
            $revision,
            $rawString,
            expectedVersion: $existing?->version,
            source: 'legacy_work_orders_ordered_qty',
            provenance: [
                'source_table' => 'work_orders',
                'source_column' => 'ordered_qty',
                'interpretation' => 'raw_display_only',
            ],
            rawValue: $rawString,
        );
        $value->update([
            'source_fingerprint' => $sourceFingerprint,
            'source_updated_at' => $job->updated_at,
        ]);

        AiDetailMigrationLedger::create([
            'entity_id' => $job->entity_id,
            'resource_type' => 'job',
            'resource_id' => $job->id,
            'attribute_key' => 'ordered_qty',
            'source_raw_value' => $rawString,
            'extracted_value' => $rawString,
            'source_fingerprint' => $sourceFingerprint,
            'status' => 'applied',
            'reason' => 'approved_raw_display_migration',
        ]);
    }

    protected function backfillJobPrintSides(WorkOrder $job, ?string $expectedFingerprint, bool $force): void
    {
        $rawSpecs = $job->specifications;
        if ($rawSpecs === null || $rawSpecs === '') {
            return;
        }

        $rawString = is_string($rawSpecs) ? $rawSpecs : json_encode($rawSpecs);
        $sourceFingerprint = hash('sha256', $rawString);

        if ($expectedFingerprint !== null && $expectedFingerprint !== $sourceFingerprint) {
            AiDetailMigrationLedger::create([
                'entity_id' => $job->entity_id,
                'resource_type' => 'job',
                'resource_id' => $job->id,
                'attribute_key' => 'print.sides',
                'source_raw_value' => $rawString,
                'source_fingerprint' => $sourceFingerprint,
                'status' => 'conflict',
                'reason' => 'fingerprint_mismatch',
                'conflict_details' => [
                    'expected' => $expectedFingerprint,
                    'actual' => $sourceFingerprint,
                ],
            ]);

            return;
        }

        $extracted = $this->extractPrintSidesValue($rawSpecs);

        if ($extracted === null) {
            // Unapproved or ambiguous shape: log conflict to ledger, do not migrate to ai_detail_attributes
            AiDetailMigrationLedger::create([
                'entity_id' => $job->entity_id,
                'resource_type' => 'job',
                'resource_id' => $job->id,
                'attribute_key' => 'print.sides',
                'source_raw_value' => $rawString,
                'source_fingerprint' => $sourceFingerprint,
                'status' => 'conflict',
                'reason' => 'unapproved_or_ambiguous_shape',
            ]);

            return;
        }

        $family = (new DefinitionWriter)->findOrCreateFamily('print.sides', [
            'label' => 'Print sides',
            'resource_type' => 'job',
            'value_kind' => 'enum',
            'entity_id' => $job->entity_id,
        ]);
        $revision = (new DefinitionWriter)->registerRevision($family, 'print.sides', 1, [
            'data_type' => 'enum',
            'value_kind' => 'enum',
            'options' => ['one_side' => '1 Side Ptg', 'two_side' => '2 Side Ptg'],
        ]);
        $existing = AiJobDetailValue::where('work_order_id', $job->id)
            ->where('definition_revision_id', $revision->id)
            ->first();

        if ($existing && ! $force) {
            if ($existing->source_updated_at && $job->updated_at && $existing->source_updated_at > $job->updated_at) {
                AiDetailMigrationLedger::create([
                    'entity_id' => $job->entity_id,
                    'resource_type' => 'job',
                    'resource_id' => $job->id,
                    'attribute_key' => 'print.sides',
                    'source_raw_value' => $rawString,
                    'source_fingerprint' => $sourceFingerprint,
                    'status' => 'stale_skipped',
                    'reason' => 'stale_source',
                ]);

                return;
            }

            if ($existing->source_fingerprint === $sourceFingerprint) {
                return; // idempotent match
            }
        }

        $typedValue = $extracted === 1 ? 'one_side' : 'two_side';
        $value = (new TypedValueWriter)->write(
            $job,
            $revision,
            $typedValue,
            expectedVersion: $existing?->version,
            source: 'legacy_work_order_specifications',
            provenance: ['source_table' => 'work_orders', 'source_column' => 'specifications'],
            rawValue: $rawString,
        );
        $value->update([
            'source_fingerprint' => $sourceFingerprint,
            'source_updated_at' => $job->updated_at,
        ]);

        AiDetailMigrationLedger::create([
            'entity_id' => $job->entity_id,
            'resource_type' => 'job',
            'resource_id' => $job->id,
            'attribute_key' => 'print.sides',
            'source_raw_value' => $rawString,
            'extracted_value' => (string) $extracted,
            'source_fingerprint' => $sourceFingerprint,
            'status' => 'applied',
            'reason' => 'approved_literal_migration',
        ]);
    }

    /**
     * Extract integer sides from approved literal shapes.
     * Returns 1, 2, or null if unapproved.
     */
    protected function extractPrintSidesValue(mixed $specs): ?int
    {
        $data = is_string($specs) ? json_decode($specs, true) : $specs;

        $oneSideApproved = ['1 side ptg', '1 side', 'one side', '1-side', 'single side', '1side'];
        $twoSideApproved = ['2 sides ptg', '2 sides', 'two sides', '2-side', 'both sides', '2side', '2 sides ptg.'];

        if (is_array($data)) {
            // The production form stores the selected print mode as a
            // top-level boolean (for example, "1 Side Ptg": true). Handle
            // those keys before the older labelled-value shapes. Lamination
            // is deliberately not a candidate: "1 Side" there describes the
            // coating, not the number of printed sides.
            $directSides = [];
            foreach ([
                '1 Side Ptg' => 1,
                '2 Side Duplex Ptg' => 2,
            ] as $key => $sides) {
                if (array_key_exists($key, $data) && $data[$key] === true) {
                    $directSides[] = $sides;
                }
            }

            if (count($directSides) > 1) {
                return null;
            }
            if ($directSides !== []) {
                return $directSides[0];
            }

            $candidateKeys = ['Printing Sides', 'Print Sides', 'Type of Printing', 'Side', 'Sides', 'Sides to Print'];
            foreach ($candidateKeys as $k) {
                if (isset($data[$k])) {
                    $val = $data[$k];
                    if (is_string($val)) {
                        $clean = strtolower(trim($val));
                        if (in_array($clean, $oneSideApproved, true)) {
                            return 1;
                        }
                        if (in_array($clean, $twoSideApproved, true)) {
                            return 2;
                        }
                    } elseif (is_array($val)) {
                        foreach ($val as $subKey => $boolVal) {
                            if ($boolVal) {
                                $clean = strtolower(trim((string) $subKey));
                                if (in_array($clean, $oneSideApproved, true)) {
                                    return 1;
                                }
                                if (in_array($clean, $twoSideApproved, true)) {
                                    return 2;
                                }
                            }
                        }
                    }
                }
            }
        } elseif (is_string($specs)) {
            $clean = strtolower(trim($specs));
            if (in_array($clean, $oneSideApproved, true)) {
                return 1;
            }
            if (in_array($clean, $twoSideApproved, true)) {
                return 2;
            }
        }

        return null;
    }
}
