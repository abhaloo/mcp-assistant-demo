<?php

declare(strict_types=1);

namespace App\Services\AskAi\PageContext;

use App\Models\Bill;
use App\Models\Customer;
use App\Models\CustomerOrder;
use App\Models\Scopes\WorkOrderVisibilityScope;
use App\Models\User;
use App\Models\WorkOrder;
use Illuminate\Auth\Access\AuthorizationException;
use Illuminate\Database\Eloquent\Builder;
use Illuminate\Database\Eloquent\ModelNotFoundException;
use Illuminate\Support\Collection;
use Illuminate\Support\Facades\Gate;
use Illuminate\Support\Facades\Log;

final class JobsPageContextResolver implements PageContextResolverInterface
{
    public function supports(ClientPageContext $client): bool
    {
        if ($client->version === 2 && $client->profile !== null) {
            return JobsContextProfile::findByProfile(2, $client->profile) !== null;
        }

        return JobsContextProfile::find(
            $client->version,
            $client->route,
            $client->kind,
            $client->resourceType,
        ) !== null;
    }

    public function resolve(ClientPageContext $client, User $user): TrustedPageContext
    {
        $profile = $client->version === 2 && $client->profile !== null
            ? JobsContextProfile::findByProfile(2, $client->profile)
            : JobsContextProfile::find(
                $client->version,
                $client->route,
                $client->kind,
                $client->resourceType,
            );

        if ($profile === null) {
            throw new InvalidPageContextException('Unsupported Jobs page_context.', 'unsupported_route_tuple');
        }

        if ($profile['requires_scope_id']) {
            $this->authorizeParent($profile['scope_kind'], (int) $client->scopeId, $user);
        }

        $records = $this->authorizeAndBuildRecords(
            $client->recordIds,
            $user,
            $profile,
            $client->scopeId,
        );

        return new TrustedPageContext(
            version: $client->version,
            kind: $client->kind,
            resourceType: $client->resourceType,
            profile: $profile['profile'],
            title: $profile['title'],
            records: $records,
            detailContract: $client->detailContract,
            definitionBundleHash: $client->definitionBundleHash,
            compatibilityEpoch: $client->compatibilityEpoch,
        );
    }

    private function authorizeParent(?string $scopeKind, int $scopeId, User $user): void
    {
        try {
            match ($scopeKind) {
                'customer' => Gate::forUser($user)->authorize('view', Customer::findOrFail($scopeId)),
                'bill' => Gate::forUser($user)->authorize('view', Bill::findOrFail($scopeId)),
                'customer_order' => Gate::forUser($user)->authorize('view', CustomerOrder::findOrFail($scopeId)),
                default => throw new StalePageContextException,
            };
        } catch (ModelNotFoundException|AuthorizationException) {
            throw new StalePageContextException;
        }
    }

    /**
     * @param  list<string>  $recordIds
     * @param  array<string, mixed>  $profile
     * @return list<PageRecord>
     */
    private function authorizeAndBuildRecords(
        array $recordIds,
        User $user,
        array $profile,
        ?string $scopeId,
    ): array {
        if ($recordIds === []) {
            return [];
        }

        $intIds = array_map(static fn (string $id): int => (int) $id, $recordIds);

        $query = WorkOrder::query()
            ->withoutGlobalScopes()
            ->with(['customer', 'department', 'bill', 'specification']);

        WorkOrderVisibilityScope::applyToBuilder($query, $user);

        $query->whereIn('id', $intIds);

        if ($profile['requires_scope_id'] && $scopeId !== null) {
            $this->applyParentMembership($query, $profile['scope_kind'], (int) $scopeId);
        }

        /** @var Collection<int, WorkOrder> $found */
        $found = $query->get()->keyBy(static fn (WorkOrder $wo): int => (int) $wo->id);

        if ($found->count() !== count($recordIds)) {
            Log::channel('ai-search')->info('page_context stale', [
                'profile' => $profile['profile'],
                'reason' => 'partial_or_unauthorized',
            ]);
            throw new StalePageContextException;
        }

        $includeDetail = in_array($profile['profile'], ['jobs.show.v1', 'jobs.show.v2'], true);
        $records = [];
        foreach ($recordIds as $id) {
            $workOrder = $found->get((int) $id);
            if (! $workOrder instanceof WorkOrder) {
                Log::channel('ai-search')->info('page_context stale', [
                    'profile' => $profile['profile'],
                    'reason' => 'missing_record',
                ]);
                throw new StalePageContextException;
            }

            try {
                Gate::forUser($user)->authorize('view', $workOrder);
            } catch (AuthorizationException) {
                Log::channel('ai-search')->info('page_context stale', [
                    'profile' => $profile['profile'],
                    'reason' => 'policy_denied',
                ]);
                throw new StalePageContextException;
            }

            $records[] = $this->buildRecord($workOrder, $includeDetail);
        }

        return $records;
    }

    private function applyParentMembership(Builder $query, ?string $scopeKind, int $scopeId): void
    {
        match ($scopeKind) {
            'customer' => $query->where('customer_id', $scopeId),
            'bill' => $query->where('bill_id', $scopeId),
            'customer_order' => $query->where('customer_order_id', $scopeId),
            default => throw new StalePageContextException,
        };
    }

    private function buildRecord(WorkOrder $workOrder, bool $includeDetail): PageRecord
    {
        $description = $workOrder->description !== null ? trim((string) $workOrder->description) : null;
        if ($description === '') {
            $description = null;
        }

        $raw = [
            'work_number' => $workOrder->work_number !== null ? (string) $workOrder->work_number : null,
            'title' => $workOrder->title !== null ? (string) $workOrder->title : null,
            'customer' => $workOrder->customer?->name !== null ? (string) $workOrder->customer->name : null,
            'bill_number' => $workOrder->bill?->invoice_number !== null
                ? (string) $workOrder->bill->invoice_number
                : null,
            'status' => $workOrder->status !== null ? (string) $workOrder->status : null,
            'department' => $workOrder->department?->name !== null ? (string) $workOrder->department->name : null,
            'priority' => $workOrder->priority !== null ? (string) $workOrder->priority : null,
            'is_high_priority' => $workOrder->priority === 'high' && (int) $workOrder->department_id !== 12
                ? 'true'
                : 'false',
            'section' => $workOrder->specification?->name !== null
                ? (string) $workOrder->specification->name
                : null,
            'delivery' => self::dateString($workOrder->delivery_date),
            'actual_delivery' => self::dateString($workOrder->actual_delivery_date),
            'ordered_qty' => $workOrder->ordered_qty !== null ? (string) $workOrder->ordered_qty : null,
            'no_pages' => $workOrder->no_pages !== null ? (string) $workOrder->no_pages : null,
            'created_at' => $workOrder->created_at?->toIso8601String(),
            'updated_at' => $workOrder->updated_at?->toIso8601String(),
        ];

        if ($includeDetail && $description !== null) {
            $raw['description_summary'] = $description;
        }

        $allowlist = JobsContextProfile::FIELD_ALLOWLIST;
        if ($includeDetail) {
            $allowlist = array_merge($allowlist, JobsContextProfile::DETAIL_EXTRA_FIELDS);
        }

        $fields = [];
        $truncated = [];
        foreach ($allowlist as $key) {
            $value = $raw[$key] ?? null;
            if ($value === null) {
                $fields[$key] = null;

                continue;
            }
            $cap = JobsContextProfile::FIELD_CAPS[$key] ?? 200;
            if (mb_strlen($value) > $cap) {
                $fields[$key] = mb_substr($value, 0, $cap);
                $truncated[] = $key;
            } else {
                $fields[$key] = $value;
            }
        }

        $workNumber = $fields['work_number'] ?? (string) $workOrder->id;
        $title = $fields['title'] ?? '';
        $label = $title !== '' && $title !== null
            ? '#'.$workNumber.' — '.$title
            : '#'.$workNumber;

        return new PageRecord(
            id: (string) (int) $workOrder->id,
            label: $label,
            fields: $fields,
            linkKey: JobsContextProfile::LINK_KEY,
            truncatedFields: $truncated,
        );
    }

    private static function dateString(mixed $value): ?string
    {
        if ($value === null) {
            return null;
        }

        if ($value instanceof \DateTimeInterface) {
            return $value->format('Y-m-d');
        }

        $str = (string) $value;
        if (! preg_match('/^\d{4}-\d{2}-\d{2}$/', $str)) {
            return null;
        }

        return $str;
    }
}
