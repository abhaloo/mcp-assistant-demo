<?php

declare(strict_types=1);

namespace App\Services\AskAi\RecordContext;

use App\Authorization\AiAccessPolicy;
use App\Authorization\BusinessRecordDefinition;
use App\Authorization\BusinessRecordRegistry;
use App\Models\User;
use Illuminate\Database\Eloquent\Model;

/**
 * Server-side resolution + trusted context build (Task B4 / R2). Takes the
 * UNTRUSTED browser ClientRecordContext, reloads the user, re-authorizes every
 * reference via AiAccessPolicy (reference AND link authorization), drops
 * unauthorized/unknown/missing references silently (forbidden indistinguishable
 * from missing — no error revealing existence), and builds the TRUSTED context
 * from server-side data only. Field allowlists come from
 * BusinessRecordDefinition::readableFields() (Task P3 — previously
 * PolicyManifestExporter::readableFields(); this class no longer depends on
 * the exporter at all, only on the registry it already held) — no second
 * allowlist. If ZERO references survive authorization, resolve() returns
 * null: the caller must send NO record_context and NO digest claim.
 */
final class RecordContextResolver
{
    /**
     * One resource type's "primary label" field, independently inventoried from
     * GlobalSearchService::registerDefaults()'s titleRenderer closures (same
     * discipline BusinessRecordDefinition::readableFields() itself follows) — always a
     * member of that resource type's own readable_fields allowlist, so this is a
     * presentation choice within the existing allowlist, not a second one.
     *
     * @var array<string, string>
     */
    private const LABEL_FIELD = [
        'invoice' => 'invoice_number',
        'quotation' => 'invoice_number',
        'payable_quotation' => 'invoice_number',
        'credit_note' => 'bill.invoice_number',
        'customer_order' => 'order_number',
        'job' => 'work_number',
        'customer' => 'name',
        'supplier' => 'name',
        'product' => 'name',
        'inventory' => 'reference',
    ];

    /**
     * Task P3: no PolicyManifestExporter dependency anymore — readable
     * fields come directly off the selected registry definition
     * (BusinessRecordDefinition::readableFields()) below.
     */
    public function __construct(
        private readonly BusinessRecordRegistry $registry = new BusinessRecordRegistry,
    ) {}

    public function resolve(ClientRecordContext $client, User $user): ?TrustedRecordContext
    {
        $policy = AiAccessPolicy::forUser(
            $user,
            $user->getAllPermissions()->pluck('name')->all(),
            $this->registry,
        );

        $title = RecordContextCaps::boundedTitle('Search results for '.$client->query);

        $records = [];
        foreach ($client->references as $reference) {
            if (count($records) >= RecordContextCaps::MAX_RECORDS) {
                break;
            }

            $record = $this->authorizeAndBuildRecord($reference, $policy);
            if ($record === null) {
                continue;
            }

            $trial = [...$records, $record];
            $trialBytes = strlen(RecordContextDigest::canonicalJsonBytes(
                (new TrustedRecordContext($title, $trial))->toWireArray()
            ));
            if ($trialBytes > RecordContextCaps::MAX_TOTAL_BYTES) {
                // Deterministic: stop at the first record that would breach the
                // total-bytes cap, keeping every record resolved so far (a
                // prefix in resolution/reference order), dropping the rest.
                break;
            }
            $records = $trial;
        }

        if ($records === []) {
            return null;
        }

        return new TrustedRecordContext($title, $records);
    }

    private function authorizeAndBuildRecord(
        ClientRecordReference $reference,
        AiAccessPolicy $policy,
    ): ?TrustedRecordContextRecord {
        if (! $this->registry->has($reference->resourceType)) {
            return null;
        }

        $definition = $this->registry->get($reference->resourceType);

        $id = (int) $reference->recordId;
        if ($id < 1) {
            return null;
        }

        $readableFields = $definition->readableFields();
        $relations = $this->relationsFor($readableFields);

        /** @var Model|null $model */
        $model = $definition->modelClass::query()
            ->withoutGlobalScopes()
            ->with($relations)
            ->find($id);

        if ($model === null) {
            return null;
        }

        $scoping = $this->scopingAttributes($definition, $model);
        if ($scoping === null) {
            return null;
        }

        if (! $policy->canView($reference->resourceType, $scoping)
            || ! $policy->canLink($reference->resourceType, $scoping)) {
            return null;
        }

        return new TrustedRecordContextRecord(
            resourceType: $reference->resourceType,
            recordId: RecordContextCaps::boundedRecordId((string) $model->getKey()),
            label: $this->buildLabel($reference->resourceType, $model),
            fields: $this->buildFields($model, $readableFields),
            linkKey: $model->getTable(),
        );
    }

    /**
     * @param  list<string>  $readableFields
     * @return list<string>
     */
    private function relationsFor(array $readableFields): array
    {
        $relations = [];
        foreach ($readableFields as $field) {
            if (str_contains($field, '.')) {
                [$relation] = explode('.', $field, 2);
                $relations[$relation] = true;
            }
        }

        return array_keys($relations);
    }

    /**
     * Plain scoping attributes for AiAccessPolicy::canView()/canLink() — resolved
     * from server-side data only. Returns null when the underlying data cannot be
     * resolved at all (e.g. an orphaned credit_note whose bill relation is
     * missing) — deliberately dropped BEFORE authorization runs, since a Super
     * Admin's entity bypass would otherwise view a record whose real entity can
     * never be verified.
     *
     * @return array<string, mixed>|null
     */
    private function scopingAttributes(BusinessRecordDefinition $definition, Model $model): ?array
    {
        if ($definition->entityScope === BusinessRecordDefinition::ENTITY_SCOPE_VIA_PARENT) {
            $parent = $definition->parentRelation !== null ? $model->{$definition->parentRelation} : null;
            if ($parent === null) {
                return null;
            }
            $entityId = $parent->entity_id ?? null;
        } else {
            $entityId = $model->entity_id ?? null;
        }

        if ($entityId === null) {
            return null;
        }

        $attributes = ['entity_id' => $entityId];

        if ($definition->entityScope === BusinessRecordDefinition::ENTITY_SCOPE_DEPARTMENT) {
            $attributes['department_id'] = $model->department_id ?? null;
        }

        foreach (array_keys($definition->recordPredicates) as $column) {
            $attributes[$column] = $model->{$column} ?? null;
        }

        return $attributes;
    }

    private function buildLabel(string $resourceType, Model $model): string
    {
        $path = self::LABEL_FIELD[$resourceType] ?? null;
        $value = $path !== null ? $this->resolveFieldValue($model, $path) : null;

        if ($value === null || trim($value) === '') {
            $value = '#'.$model->getKey();
        }

        return RecordContextCaps::boundedLabel($value);
    }

    /**
     * @param  list<string>  $readableFields
     * @return array<string, string>
     */
    private function buildFields(Model $model, array $readableFields): array
    {
        $pairs = [];
        foreach ($readableFields as $field) {
            if ($field === 'id') {
                continue;
            }

            $value = $this->resolveFieldValue($model, $field);
            if ($value === null || $value === '') {
                continue;
            }

            $pairs[] = [$this->wireKeyFor($field), $value];
        }

        return RecordContextCaps::boundedFields($pairs);
    }

    private function wireKeyFor(string $field): string
    {
        if (str_contains($field, '.')) {
            [$relation] = explode('.', $field, 2);

            return $relation;
        }

        return $field;
    }

    private function resolveFieldValue(Model $model, string $path): ?string
    {
        if (str_contains($path, '.')) {
            [$relation, $column] = explode('.', $path, 2);
            $related = $model->{$relation} ?? null;
            if ($related === null) {
                return null;
            }
            $value = $related->{$column} ?? null;
        } else {
            $value = $model->{$path} ?? null;
        }

        if ($value === null) {
            return null;
        }

        return (string) $value;
    }
}
