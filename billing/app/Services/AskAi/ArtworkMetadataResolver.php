<?php

declare(strict_types=1);

namespace App\Services\AskAi;

use App\Models\User;
use App\Models\WorkOrder;
use App\Models\WorkOrderAttachment;

final class ArtworkMetadataResolver
{
    /**
     * Resolves sanitized artwork metadata for an authorized job.
     * Sensitive filesystem paths are strictly excluded (ADR 0022 / ADR 0027).
     *
     * @return list<array<string, mixed>>
     */
    public function getArtworkMetadataForJob(WorkOrder $job, User $user): array
    {
        // Fail-closed authorization check
        if (! $user->can('view job')) {
            return [];
        }

        if (! $user->hasRole('Super Admin')) {
            if ((int) $job->entity_id !== (int) $user->entity_id) {
                return [];
            }
            if ($user->department_id !== null && (int) $job->department_id !== (int) $user->department_id) {
                return [];
            }
        }

        $attachments = WorkOrderAttachment::where('work_order_id', $job->id)
            ->get();

        $result = [];
        foreach ($attachments as $attachment) {
            $result[] = [
                'id' => (int) $attachment->id,
                'format' => (string) $attachment->extension,
                'size_bytes' => (int) $attachment->size,
                'description' => $attachment->description,
                'status' => $attachment->trashed() ? 'archived' : 'active',
                'created_at' => $attachment->created_at?->toISOString(),
            ];
        }

        return $result;
    }
}
