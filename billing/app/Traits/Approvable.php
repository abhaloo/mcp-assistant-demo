<?php

namespace App\Traits;

use App\Models\ApprovalRequest;
use App\Models\User;
use App\Notifications\ApprovalRequestedNotification;
use App\Support\NotificationDispatch;
use Illuminate\Database\Eloquent\Relations\MorphMany;
use Illuminate\Support\Facades\DB;

trait Approvable
{
    /**
     * Get all approval requests for this model.
     */
    public function approvalRequests(): MorphMany
    {
        return $this->morphMany(ApprovalRequest::class, 'approvable');
    }

    /**
     * Check if this model has a pending approval request.
     *
     * @param  string|null  $type  Specific action type to check, or null for any type
     */
    public function hasPendingApproval(?string $type = null): bool
    {
        $query = $this->approvalRequests()->pending();

        if ($type) {
            $query->forType($type);
        }

        return $query->exists();
    }

    /**
     * Get the latest pending approval request.
     *
     * @param  string|null  $type  Specific action type to get, or null for any type
     */
    public function pendingApproval(?string $type = null): ?ApprovalRequest
    {
        $query = $this->approvalRequests()->pending();

        if ($type) {
            $query->forType($type);
        }

        return $query->latest()->first();
    }

    /**
     * Create a pending department-move approval and mark the job atomically.
     *
     * @param  array<string, mixed>  $payload
     * @param  array<string, mixed>  $pendingAttributes  Extra model fields to set while pending (e.g. priority)
     */
    public function requestDepartmentMoveApproval(
        array $payload,
        ?string $reason = null,
        array $pendingAttributes = []
    ): ApprovalRequest {
        return $this->requestApproval(
            ApprovalRequest::TYPE_DEPARTMENT_MOVE,
            $payload,
            $reason,
            function ($model) use ($pendingAttributes) {
                $model->update(array_merge(
                    ['status' => 'PENDING MOVE APPROVAL'],
                    $pendingAttributes
                ));
            }
        );
    }

    /**
     * Create a new approval request for this model.
     *
     * @param  string  $type  Action type constant from ApprovalRequest
     * @param  array  $payload  Action-specific data
     * @param  string|null  $reason  User's reason for the request
     * @param  callable|null  $withinTransaction  Runs after the request row is created, inside the same transaction
     *
     * @throws \Exception If a pending request already exists
     */
    public function requestApproval(
        string $type,
        array $payload,
        ?string $reason = null,
        ?callable $withinTransaction = null
    ): ApprovalRequest {
        if ($this->hasPendingApproval($type)) {
            throw new \Exception('A pending approval request already exists for this action.');
        }

        $approvalRequest = DB::transaction(function () use ($type, $payload, $reason, $withinTransaction) {
            $approvalRequest = $this->approvalRequests()->create([
                'action_type' => $type,
                'payload' => $payload,
                'requested_by' => auth()->id(),
                'reason' => $reason,
                'status' => ApprovalRequest::PENDING,
            ]);

            if ($withinTransaction) {
                $withinTransaction($this, $approvalRequest);
            }

            return $approvalRequest;
        });

        $approvers = User::permission('approve job requests')
            ->where('entity_id', $approvalRequest->entity_id)
            ->where('id', '!=', auth()->id())
            ->get();

        NotificationDispatch::send(
            $approvers,
            new ApprovalRequestedNotification($approvalRequest, auth()->user())
        );

        return $approvalRequest;
    }
}
