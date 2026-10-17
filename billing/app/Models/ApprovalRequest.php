<?php

namespace App\Models;

use App\Notifications\ApprovalReviewedNotification;
use App\Notifications\DepartmentMoveNotification;
use App\Support\NotificationDispatch;
use App\Traits\EntityTrait;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Database\Eloquent\Relations\MorphTo;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Log;

class ApprovalRequest extends Model
{
    use EntityTrait;

    protected $guarded = [];

    protected $casts = [
        'payload' => 'array',
        'reviewed_at' => 'datetime',
    ];

    // Action type constants
    const TYPE_DEPARTMENT_MOVE = 'department_move';

    // Status constants
    const PENDING = 'pending';

    const APPROVED = 'approved';

    const REJECTED = 'rejected';

    /*
    |--------------------------------------------------------------------------
    | Relationships
    |--------------------------------------------------------------------------
    */

    public function approvable(): MorphTo
    {
        return $this->morphTo();
    }

    public function requestedBy(): BelongsTo
    {
        return $this->belongsTo(User::class, 'requested_by');
    }

    public function reviewedBy(): BelongsTo
    {
        return $this->belongsTo(User::class, 'reviewed_by');
    }

    /*
    |--------------------------------------------------------------------------
    | Scopes
    |--------------------------------------------------------------------------
    */

    public function scopePending($query)
    {
        return $query->where('status', self::PENDING);
    }

    public function scopeForType($query, string $type)
    {
        return $query->where('action_type', $type);
    }

    /*
    |--------------------------------------------------------------------------
    | Status Helpers
    |--------------------------------------------------------------------------
    */

    public function isPending(): bool
    {
        return $this->status === self::PENDING;
    }

    public function isApproved(): bool
    {
        return $this->status === self::APPROVED;
    }

    public function isRejected(): bool
    {
        return $this->status === self::REJECTED;
    }

    public function statusBadge(): string
    {
        return match ($this->status) {
            self::PENDING => '<span class="badge bg-warning">Pending</span>',
            self::APPROVED => '<span class="badge bg-success">Approved</span>',
            self::REJECTED => '<span class="badge bg-danger">Rejected</span>',
            default => '<span class="badge bg-secondary">Unknown</span>',
        };
    }

    /*
    |--------------------------------------------------------------------------
    | Action Type Labels
    |--------------------------------------------------------------------------
    */

    public function actionLabel(): string
    {
        return match ($this->action_type) {
            self::TYPE_DEPARTMENT_MOVE => 'Department Move',
            default => ucwords(str_replace('_', ' ', $this->action_type)),
        };
    }

    public function actionDescription(): string
    {
        if ($this->action_type === self::TYPE_DEPARTMENT_MOVE) {
            $fromDept = Department::find($this->payload['from_department_id'] ?? null);
            $toDept = Department::find($this->payload['to_department_id'] ?? null);

            return sprintf(
                'Move job from "%s" to "%s"',
                $fromDept->name ?? 'Unknown',
                $toDept->name ?? 'Unknown'
            );
        }

        return 'Action pending approval';
    }

    /**
     * Human-readable label for the related approvable, used in notification payloads.
     * Falls back to the action label if no approvable is attached.
     */
    public function subjectLabel(): string
    {
        $approvable = $this->approvable;

        if ($approvable instanceof WorkOrder && $approvable->job_number) {
            return "Job #{$approvable->job_number}";
        }

        return $this->actionLabel();
    }

    /*
    |--------------------------------------------------------------------------
    | Core Actions
    |--------------------------------------------------------------------------
    */

    /**
     * Approve the request and execute the action.
     * Uses transaction and row locking to prevent race conditions.
     */
    public function approve(User $reviewer, ?string $notes = null): bool
    {
        $result = DB::transaction(function () use ($reviewer, $notes) {
            // Lock row to prevent concurrent approvals
            $locked = self::lockForUpdate()->find($this->id);

            if (! $locked || ! $locked->isPending()) {
                Log::warning('Attempted to approve non-pending request', [
                    'request_id' => $this->id,
                    'current_status' => $locked->status ?? 'not found',
                    'reviewer_id' => $reviewer->id,
                ]);

                return false;
            }

            // Update status
            $locked->update([
                'status' => self::APPROVED,
                'reviewed_by' => $reviewer->id,
                'review_notes' => $notes,
                'reviewed_at' => now(),
            ]);

            // Execute the approved action
            $locked->executeApprovedAction();

            Log::info('Approval request approved', [
                'request_id' => $this->id,
                'action_type' => $this->action_type,
                'reviewer_id' => $reviewer->id,
            ]);

            return true;
        });

        // Dispatch notifications AFTER transaction commits
        if ($result) {
            $this->refresh();
            $this->dispatchApprovalNotifications($reviewer);
        }

        return $result;
    }

    /**
     * Reject the request with required notes.
     */
    public function reject(User $reviewer, string $notes): bool
    {
        if (! $this->isPending()) {
            Log::warning('Attempted to reject non-pending request', [
                'request_id' => $this->id,
                'current_status' => $this->status,
                'reviewer_id' => $reviewer->id,
            ]);

            return false;
        }

        $this->update([
            'status' => self::REJECTED,
            'reviewed_by' => $reviewer->id,
            'review_notes' => $notes,
            'reviewed_at' => now(),
        ]);

        // Restore original status if this is a department move request
        if ($this->action_type === self::TYPE_DEPARTMENT_MOVE && $this->approvable) {
            $previousStatus = $this->payload['previous_status'] ?? 'IN PROGRESS';
            $this->approvable->update(['status' => $previousStatus]);
        }

        Log::info('Approval request rejected', [
            'request_id' => $this->id,
            'action_type' => $this->action_type,
            'reviewer_id' => $reviewer->id,
        ]);

        // Notify the requester
        $this->dispatchApprovalNotifications($reviewer);

        return true;
    }

    /**
     * Send notifications after an approval is reviewed (approved or rejected).
     */
    protected function dispatchApprovalNotifications(?User $actor = null): void
    {
        // Notify the requester that their request was reviewed
        $requester = $this->requestedBy;
        if ($requester) {
            NotificationDispatch::send(
                $requester,
                new ApprovalReviewedNotification($this, $actor)
            );
        }

        // For department moves, also notify assigned work order users
        if ($this->isApproved() && $this->action_type === self::TYPE_DEPARTMENT_MOVE) {
            $workOrder = $this->approvable;
            if ($workOrder instanceof WorkOrder) {
                $fromDept = Department::find($this->payload['from_department_id']);
                $toDept = Department::find($this->payload['to_department_id']);

                $recipients = $workOrder->users()
                    ->where('users.entity_id', $this->entity_id)
                    ->where('users.id', '!=', $this->requested_by)
                    ->get();

                if ($recipients->isNotEmpty() && $fromDept && $toDept) {
                    NotificationDispatch::send(
                        $recipients,
                        new DepartmentMoveNotification($workOrder, $fromDept->name, $toDept->name, $actor)
                    );
                }
            }
        }
    }

    /*
    |--------------------------------------------------------------------------
    | Action Execution
    |--------------------------------------------------------------------------
    */

    /**
     * Execute the approved action based on action_type.
     * Add new action types here as needed.
     */
    protected function executeApprovedAction(): void
    {
        match ($this->action_type) {
            self::TYPE_DEPARTMENT_MOVE => $this->executeDepartmentMove(),
            default => throw new \Exception("Unknown action type: {$this->action_type}"),
        };
    }

    /**
     * Execute department move for a work order.
     */
    protected function executeDepartmentMove(): void
    {
        $workOrder = $this->approvable;
        $payload = $this->payload;

        if (! $workOrder instanceof WorkOrder) {
            throw new \Exception('Invalid approvable type for department move');
        }

        $prevDepartment = $workOrder->department;
        $newDepartment = Department::findOrFail($payload['to_department_id']);

        // Determine the appropriate status based on target department
        $newStatus = $this->determineStatusForDepartment($newDepartment, $payload['mark_delivered'] ?? false);

        // Update work order
        $workOrder->update([
            'department_id' => $newDepartment->id,
            'status' => $newStatus,
            'read_status' => false,
        ]);

        // Create work message for audit trail
        $section = $prevDepartment->name.' <i class="fa fa-arrow-right"></i> '.$newDepartment->name;

        // Determine appropriate message status
        $messageStatus = $this->determineMessageStatusForDepartment($newDepartment, $payload['mark_delivered'] ?? false);

        WorkMessage::create([
            'work_order_id' => $workOrder->id,
            'section' => $section,
            'status' => $messageStatus,
            'message' => '[Approved by '.$this->reviewedBy->name.'] '.($payload['remark'] ?? ''),
            'assigned_by' => $this->requestedBy->name,
        ]);
    }

    /**
     * Determine the appropriate status for a work order based on target department.
     */
    protected function determineStatusForDepartment(Department $department, bool $markDelivered): string
    {
        // REJECTED & CANCELLED department (ID 14) should set status to CANCELLED
        if ($department->id === 14) {
            return 'CANCELLED';
        }

        return $markDelivered ? 'FINISHED' : 'IN PROGRESS';
    }

    /**
     * Determine the appropriate message status based on target department.
     */
    protected function determineMessageStatusForDepartment(Department $department, bool $markDelivered): string
    {
        if ($department->id === 14) {
            return 'CANCELLED';
        }

        return $markDelivered ? 'DELIVERED' : 'FINISHED';
    }

    /*
    |--------------------------------------------------------------------------
    | Boot
    |--------------------------------------------------------------------------
    */

    protected static function booted()
    {
        static::creating(function ($model) {
            static::assignEntityIdIfMissing($model);
        });
    }
}
