<?php

declare(strict_types=1);

namespace App\Services\AskAi\DetailAttributes;

use App\Models\AiAttributeFamily;
use App\Models\AiDefinitionRevision;
use Illuminate\Support\Facades\DB;
use InvalidArgumentException;

final class DefinitionWriter
{
    /** @param array{label?: string, description?: string, entity_id?: int, resource_type?: string, value_kind?: string, status?: string, sensitivity?: string} $attributes */
    public function findOrCreateFamily(string $name, array $attributes = []): AiAttributeFamily
    {
        $entityId = $attributes['entity_id'] ?? null;
        if ($entityId === null) {
            throw new InvalidArgumentException('Attribute family entity_id is required.');
        }

        return AiAttributeFamily::firstOrCreate(
            ['name' => $name, 'entity_id' => $entityId],
            [
                'label' => $attributes['label'] ?? ucwords(str_replace('_', ' ', $name)),
                'description' => $attributes['description'] ?? null,
                'resource_type' => $attributes['resource_type'] ?? 'job',
                'value_kind' => $attributes['value_kind'] ?? 'string',
                'status' => $attributes['status'] ?? 'published',
                'sensitivity' => $attributes['sensitivity'] ?? 'internal',
                'entity_id' => $entityId,
            ]
        );
    }

    /** @param array{data_type?: string, metadata?: array<string, mixed>, entity_id?: int, resource_type?: string, value_kind?: string, label?: string, aliases?: list<string>, unit?: string, options?: array<string, mixed>} $attributes */
    public function registerRevision(
        AiAttributeFamily $family,
        string $attributeKey,
        int $revision = 1,
        array $attributes = [],
    ): AiDefinitionRevision {
        return DB::transaction(function () use ($family, $attributeKey, $revision, $attributes) {
            $familyEntityId = $family->entity_id;
            if ($familyEntityId === null) {
                throw new InvalidArgumentException('Attribute family entity_id is required.');
            }
            if (isset($attributes['entity_id']) && (int) $attributes['entity_id'] !== (int) $familyEntityId) {
                throw new InvalidArgumentException('Definition revision entity_id must match its attribute family.');
            }

            $dataType = $attributes['data_type'] ?? $family->value_kind ?? 'string';
            $valueKind = $attributes['value_kind'] ?? $family->value_kind ?? $dataType;
            $label = $attributes['label'] ?? ucwords(str_replace(['.', '_'], ' ', $attributeKey));
            $aliases = array_values($attributes['aliases'] ?? []);
            $unit = $attributes['unit'] ?? (($attributes['metadata'] ?? [])['unit'] ?? null);
            $options = $attributes['options'] ?? (($attributes['metadata'] ?? [])['options'] ?? null);
            $metadata = $attributes['metadata'] ?? [];
            $payload = [
                'family' => $family->name,
                'attribute_key' => $attributeKey,
                'revision' => $revision,
                'resource_type' => $attributes['resource_type'] ?? $family->resource_type ?? 'job',
                'data_type' => $dataType,
                'value_kind' => $valueKind,
                'label' => $label,
                'aliases' => $aliases,
                'unit' => $unit,
                'options' => $options,
                'metadata' => $this->sortRecursive($metadata),
            ];
            // The revision hash identifies the global immutable semantic
            // definition, not a tenant. Tenant ownership is carried by the
            // family/revision row and every observation scope; the migration
            // therefore permits the same content hash once per entity.
            $hash = 'sha256:'.hash('sha256', json_encode($payload, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR));

            $existing = AiDefinitionRevision::where('attribute_family_id', $family->id)
                ->where('attribute_key', $attributeKey)
                ->where('revision', $revision)
                ->first();

            if ($existing !== null) {
                if ($existing->revision_hash !== $hash) {
                    throw new \LogicException("Definition revision {$family->name}.{$attributeKey}@{$revision} is immutable.");
                }

                return $existing;
            }

            return AiDefinitionRevision::create([
                'attribute_family_id' => $family->id,
                'attribute_key' => $attributeKey,
                'resource_type' => $payload['resource_type'],
                'data_type' => $dataType,
                'value_kind' => $valueKind,
                'revision' => $revision,
                'revision_hash' => $hash,
                'label' => $label,
                'aliases' => $aliases,
                'unit' => $unit,
                'options' => $options,
                'is_active' => true,
                'published_at' => now(),
                'entity_id' => $familyEntityId,
                'metadata' => $metadata,
            ]);
        });
    }

    /** @param array<string, mixed> $value @return array<string, mixed> */
    private function sortRecursive(array $value): array
    {
        foreach ($value as $key => $item) {
            if (is_array($item)) {
                $value[$key] = $this->sortRecursive($item);
            }
        }

        ksort($value);

        return $value;
    }
}
