<?php

declare(strict_types=1);

namespace App\Services\AskAi\DetailAttributes;

use App\Models\AiDefinitionRevision;
use App\Models\AiJobDetailValue;
use App\Models\User;
use App\Models\WorkOrder;
use Illuminate\Auth\Access\AuthorizationException;
use Illuminate\Support\Facades\DB;

final class TypedValueWriter
{
    /**
     * Write typed attribute value with parent authorization enforcement,
     * scope copying from parent job, and optimistic locking.
     *
     * @throws AuthorizationException
     * @throws OptimisticLockException
     */
    public function write(
        WorkOrder $job,
        AiDefinitionRevision $revision,
        mixed $value,
        ?int $expectedVersion = null,
        ?User $actingUser = null,
        string $source = 'manual',
        ?array $provenance = null,
        ?string $rawValue = null,
    ): AiJobDetailValue {
        $jobEntityId = $job->entity_id;
        $revisionEntityId = $revision->entity_id;
        if ($jobEntityId === null) {
            throw new \InvalidArgumentException('Parent job entity_id is required.');
        }
        if ($revisionEntityId === null) {
            throw new \InvalidArgumentException('Definition revision entity_id is required.');
        }

        // Enforce authorization order: parent authorization before field/revision lookup
        if ($actingUser !== null) {
            $this->authorizeParent($job, $actingUser);
        }

        return DB::transaction(function () use ($job, $revision, $value, $expectedVersion, $source, $provenance, $rawValue, $jobEntityId, $revisionEntityId) {
            if ((int) $revisionEntityId !== (int) $jobEntityId
                || ($revision->resource_type ?? 'job') !== 'job') {
                throw new \InvalidArgumentException('Definition revision does not apply to this job.');
            }

            /** @var AiJobDetailValue|null $existing */
            $existing = AiJobDetailValue::where('work_order_id', $job->id)
                ->where('definition_revision_id', $revision->id)
                ->lockForUpdate()
                ->first();

            if ($existing !== null) {
                if ($expectedVersion !== null && $existing->version !== $expectedVersion) {
                    throw new OptimisticLockException(
                        "Optimistic lock conflict for job {$job->id}, revision {$revision->id}: expected {$expectedVersion}, found {$existing->version}."
                    );
                }

                $fields = $this->formatValueFields($revision->data_type, $value);
                $fields['entity_id'] = $jobEntityId;
                $fields['department_id'] = $job->department_id;
                $fields['revision_hash'] = $revision->revision_hash;
                $fields['raw_value'] = $rawValue ?? $this->rawValue($value);
                $fields['display_value'] = $this->displayValue($revision, $value);
                $fields['source'] = $source;
                $fields['parser_version'] = $provenance['parser_version'] ?? null;
                $fields['confidence'] = $provenance['confidence'] ?? null;
                $fields['reviewer_id'] = $provenance['reviewer_id'] ?? null;
                $fields['provenance'] = $provenance ?? ['source' => $source];
                $fields['validation_state'] = 'validated';
                $fields['version'] = $existing->version + 1;

                $existing->update($fields);

                return $existing->fresh();
            }

            if ($expectedVersion !== null && $expectedVersion !== 0 && $expectedVersion !== 1) {
                throw new OptimisticLockException(
                    "Optimistic lock conflict for job {$job->id}, revision {$revision->id}: record does not exist but expected version was {$expectedVersion}."
                );
            }

            $fields = $this->formatValueFields($revision->data_type, $value);
            $fields['work_order_id'] = $job->id;
            $fields['definition_revision_id'] = $revision->id;
            $fields['entity_id'] = $jobEntityId;
            $fields['department_id'] = $job->department_id;
            $fields['revision_hash'] = $revision->revision_hash;
            $fields['raw_value'] = $rawValue ?? $this->rawValue($value);
            $fields['display_value'] = $this->displayValue($revision, $value);
            $fields['source'] = $source;
            $fields['parser_version'] = $provenance['parser_version'] ?? null;
            $fields['confidence'] = $provenance['confidence'] ?? null;
            $fields['reviewer_id'] = $provenance['reviewer_id'] ?? null;
            $fields['provenance'] = $provenance ?? ['source' => $source];
            $fields['validation_state'] = 'validated';
            $fields['version'] = 1;

            return AiJobDetailValue::create($fields);
        });
    }

    private function authorizeParent(WorkOrder $job, User $user): void
    {
        if ($user->entity_id === null) {
            throw new AuthorizationException('User entity_id is required.');
        }

        if ((int) $job->entity_id !== (int) $user->entity_id) {
            throw new AuthorizationException('Cross-entity job access is forbidden.');
        }

        if (! $user->can('view job')) {
            throw new AuthorizationException('User lacks view job permission.');
        }

        if ($user->department_id !== null && $job->department_id !== null && (int) $job->department_id !== (int) $user->department_id) {
            throw new AuthorizationException('User lacks department access to job.');
        }
    }

    /**
     * @return array{value_text: ?string, value_number: ?string, value_boolean: ?bool, value_json: ?array<string, mixed>}
     */
    private function formatValueFields(string $dataType, mixed $value): array
    {
        $fields = [
            'value_text' => null,
            'value_number' => null,
            'value_boolean' => null,
            'value_json' => null,
        ];

        if ($value === null) {
            return $fields;
        }

        switch (strtolower($dataType)) {
            case 'integer':
            case 'int':
            case 'decimal':
            case 'float':
            case 'number':
            case 'numeric':
                $fields['value_number'] = is_numeric($value) ? (string) $value : null;
                break;

            case 'boolean':
            case 'bool':
                $fields['value_boolean'] = (bool) $value;
                break;

            case 'json':
            case 'array':
                $fields['value_json'] = is_array($value) ? $value : json_decode((string) $value, true);
                break;

            case 'string':
            case 'text':
            default:
                $fields['value_text'] = (string) $value;
                break;
        }

        return $fields;
    }

    private function rawValue(mixed $value): ?string
    {
        if ($value === null) {
            return null;
        }

        return is_scalar($value) ? (string) $value : json_encode($value, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    }

    private function displayValue(AiDefinitionRevision $revision, mixed $value): ?string
    {
        if (is_string($value) && is_array($revision->options) && array_key_exists($value, $revision->options)) {
            return (string) $revision->options[$value];
        }

        return $this->rawValue($value);
    }
}
