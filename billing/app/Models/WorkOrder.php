<?php

namespace App\Models;

use App\Helpers\BadgeHelper;
use App\Models\Concerns\LogsModelActivity;
use App\Models\Scopes\WorkOrderVisibilityScope;
use App\Traits\Approvable;
use App\Traits\EntityTrait;
use IFRS\Models\Entity;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Database\Query\Builder;
use Illuminate\Support\Collection;
use Illuminate\Support\Facades\DB;

class WorkOrder extends Model
{
    use Approvable;
    use EntityTrait;
    use HasFactory;
    use LogsModelActivity;

    protected $guarded = false;

    public function entity()
    {
        return $this->belongsTo(Entity::class);
    }

    public function department()
    {
        return $this->belongsTo(Department::class);
    }

    public function items()
    {
        return $this->hasMany(WorkOrderItem::class);
    }

    public function specification(): BelongsTo
    {
        return $this->belongsTo(Specification::class);
    }

    public function messages()
    {
        return $this->hasMany(WorkMessage::class);
    }

    public function materials()
    {
        return $this->hasMany(Inventory::class);
    }

    public function bill()
    {
        return $this->belongsTo(Bill::class);
    }

    public function customerOrder(): BelongsTo
    {
        return $this->belongsTo(CustomerOrder::class);
    }

    public function customer()
    {
        return $this->belongsTo(Customer::class);
    }

    public function getEffectiveBillAttribute()
    {
        return $this->bill ?? $this->customerOrder?->bill;
    }

    public function users()
    {
        return $this->belongsToMany(User::class);
    }

    /**
     * Status badge colors keyed by status value.
     * Spectrum: grey (new) → blue (active) → amber (waiting) → violet (review)
     *           → sky (billed) → green (done). Red for cancelled.
     * Warm tones (amber/green) break the cool wash and give the list real variety.
     */
    private const STATUS_COLORS = [
        'CREATED' => ['color' => 'secondary', 'label' => 'CREATED'],
        'IN PROGRESS' => ['color' => 'info',      'label' => 'IN PROGRESS'],
        'SUBMITTED' => ['color' => 'warning',   'label' => 'SUBMITTED'],
        'PENDING MOVE APPROVAL' => ['color' => 'violet',    'label' => 'PENDING APPROVAL', 'icon' => 'fa-clock'],
        'INVOICED' => ['color' => 'sky',       'label' => 'INVOICED'],
        'FINISHED' => ['color' => 'success',   'label' => 'FINISHED'],
        'CANCELLED' => ['color' => 'danger',    'label' => 'CANCELLED'],
    ];

    private const DEFAULT_STATUS_COLOR = ['color' => 'secondary', 'label' => 'UNKNOWN'];

    /** Semantic MCP status kind for a raw status string. */
    public static function statusKindFor(string $status): string
    {
        $color = (self::STATUS_COLORS[$status] ?? self::DEFAULT_STATUS_COLOR)['color'];

        return match ($color) {
            'success' => 'success',
            'danger' => 'danger',
            'warning' => 'warning',
            'info', 'cyan' => 'info',
            'sky' => 'sky',
            'violet' => 'violet',
            'primary' => 'primary',
            default => 'neutral',
        };
    }

    /** Display label for a raw status string. */
    public static function statusLabelFor(string $status): string
    {
        $s = self::STATUS_COLORS[$status] ?? self::DEFAULT_STATUS_COLOR;

        return $s['label'] ?? $status;
    }

    public function statusKind(): string
    {
        return self::statusKindFor($this->status);
    }

    public function statusLabel(): string
    {
        return self::statusLabelFor($this->status);
    }

    /** Render the standard status pill for a raw status string (e.g. groupBy aggregates). */
    public static function statusBadge(string $status): string
    {
        $s = self::STATUS_COLORS[$status] ?? self::DEFAULT_STATUS_COLOR;

        return BadgeHelper::render($s['label'] ?? $status, $s['color'], $s['icon'] ?? null);
    }

    public function getStatus()
    {
        return self::statusBadge($this->status);
    }

    /**
     * Active production jobs ranked by time since last activity, for the dashboard.
     * Raw DB::table query (mechanical move from HomeController) — joins work_messages
     * for last-activity and emulates TIMESTAMPDIFF on SQLite for the test suite.
     *
     * @return Collection<int, object>
     */
    public static function staleProductionRows(): Collection
    {
        return static::staleProductionSubquery()
            ->orderByDesc('hours_in_dept')
            ->get();
    }

    /** Recently inactive production jobs for the dashboard needs-attention card. */
    public static function staleNeedsAttentionRows(int $limit = 8): Collection
    {
        return DB::query()
            ->fromSub(static::staleProductionSubquery(), 'stale')
            ->where('days_inactive', '<', 90)
            ->orderByDesc('hours_in_dept')
            ->limit($limit)
            ->get();
    }

    /** Count of likely-abandoned production jobs (90+ days inactive). */
    public static function abandonedProductionCount(): int
    {
        return DB::query()
            ->fromSub(static::staleProductionSubquery(), 'stale')
            ->where('days_inactive', '>=', 90)
            ->count();
    }

    /** Preview rows for the collapsed likely-abandoned dashboard section. */
    public static function abandonedProductionPreview(int $limit = 10): Collection
    {
        return DB::query()
            ->fromSub(static::staleProductionSubquery(), 'stale')
            ->where('days_inactive', '>=', 90)
            ->orderByDesc('hours_in_dept')
            ->limit($limit)
            ->get();
    }

    /** @return Builder */
    private static function staleProductionSubquery()
    {
        $driver = DB::connection()->getDriverName();
        $hoursDiff = function (string $startExpr, string $alias) use ($driver): string {
            return $driver === 'sqlite'
                ? "CAST((julianday(CURRENT_TIMESTAMP) - julianday($startExpr)) * 24 AS INTEGER) as $alias"
                : "TIMESTAMPDIFF(HOUR, $startExpr, NOW()) as $alias";
        };
        $daysDiff = function (string $startExpr, string $alias) use ($driver): string {
            return $driver === 'sqlite'
                ? "CAST(julianday(CURRENT_TIMESTAMP) - julianday($startExpr) AS INTEGER) as $alias"
                : "TIMESTAMPDIFF(DAY, $startExpr, NOW()) as $alias";
        };

        return DB::table('work_orders as wo')
            ->leftJoin(DB::raw(
                '(SELECT work_order_id, MAX(created_at) as last_msg_at, COUNT(*) as msg_count FROM work_messages GROUP BY work_order_id) as wm'
            ), 'wo.id', '=', 'wm.work_order_id')
            ->leftJoin('customers as c', 'wo.customer_id', '=', 'c.id')
            ->leftJoin('departments as d', 'wo.department_id', '=', 'd.id')
            ->select(
                'wo.id', 'wo.work_order_number', 'wo.title', 'wo.status',
                'wo.department_id', 'wo.priority',
                'c.name as customer_name',
                'd.name as department_name',
                DB::raw($hoursDiff('COALESCE(wm.last_msg_at, wo.created_at)', 'hours_in_dept')),
                DB::raw($daysDiff('COALESCE(wm.last_msg_at, wo.created_at)', 'days_inactive')),
                DB::raw('COALESCE(wm.msg_count, 0) as message_count')
            )
            ->whereIn('wo.status', ['IN PROGRESS', 'CREATED', 'SUBMITTED', 'PENDING MOVE APPROVAL'])
            ->whereNotIn('wo.department_id', Department::NON_ACTIONABLE);
    }

    /** Jobs sitting in the pickup department awaiting collection, longest-waiting first. */
    public static function awaitingPickupRows(): Collection
    {
        $driver = DB::connection()->getDriverName();
        $hoursDiff = function (string $startExpr, string $alias) use ($driver): string {
            return $driver === 'sqlite'
                ? "CAST((julianday(CURRENT_TIMESTAMP) - julianday($startExpr)) * 24 AS INTEGER) as $alias"
                : "TIMESTAMPDIFF(HOUR, $startExpr, NOW()) as $alias";
        };

        return DB::table('work_orders as wo')
            ->leftJoin(DB::raw(
                '(SELECT work_order_id, MAX(created_at) as last_msg_at FROM work_messages GROUP BY work_order_id) as wm2'
            ), 'wo.id', '=', 'wm2.work_order_id')
            ->leftJoin('customers as c', 'wo.customer_id', '=', 'c.id')
            ->select(
                'wo.id', 'wo.work_order_number', 'wo.title',
                'c.name as customer_name', 'c.phone as customer_phone',
                DB::raw($hoursDiff('COALESCE(wm2.last_msg_at, wo.created_at)', 'hours_waiting'))
            )
            ->where('wo.department_id', Department::PICKUP)
            ->whereIn('wo.status', ['IN PROGRESS', 'SUBMITTED', 'CREATED'])
            ->orderByDesc('hours_waiting')
            ->limit(10)
            ->get();
    }

    public function user()
    {
        return $this->belongsTo(User::class);
    }

    public function getPriorityBadge()
    {
        return match ($this->priority ?? 'normal') {
            'high' => BadgeHelper::render('HIGH PRIORITY', 'danger', 'fa-exclamation'),
            default => '',
        };
    }

    protected static function booted()
    {
        static::addGlobalScope(new WorkOrderVisibilityScope);

        static::creating(function ($model) {
            static::assignEntityIdIfMissing($model);
        });
    }
}
