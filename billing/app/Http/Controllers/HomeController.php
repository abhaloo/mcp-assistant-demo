<?php

namespace App\Http\Controllers;

use App\Actions\Sidebar\BuildSidebarMenu;
use App\Models\Account;
use App\Models\ApprovalRequest;
use App\Models\Bill;
use App\Models\Department;
use App\Models\Product;
use App\Models\WorkOrder;
use App\Support\DashboardPeriod;
use Carbon\Carbon;
use Illuminate\Pagination\LengthAwarePaginator;
use Illuminate\Support\Facades\Cache;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Session;

class HomeController extends Controller
{
    private const DASHBOARD_CACHE_TTL = 120;

    /**
     * Account types whose debit journal entries count as a payment against a bill.
     * Single source of truth mirroring Bill::payments(); reused by every
     * outstanding/paid calculation below.
     */
    private const PAYMENT_ACCOUNT_TYPES = [
        Account::OPERATING_EXPENSE,
        Account::PAYABLE,
        Account::CASH,
        Account::BANK,
    ];

    public function __construct()
    {
        $this->middleware('auth');

    }

    public function index()
    {
        // this month
        $sales = 0;
        $qsales = 0;
        $qbills = null;
        $revenue = 0;
        $revenueLast = 0;
        $outstanding = 0;
        $overdueCount = 0;
        $overdueAmount = 0;
        $revenueSeries = [];
        $inProduction = 0;
        $readyPickup = 0;

        $period = DashboardPeriod::normalize((string) request('period', 'month'));

        // Uniform "to-date" window: $end is always end of today so every period
        // (week/month/quarter) excludes future days. Only the start is period-specific.
        [$start, $end] = DashboardPeriod::window($period);
        [$startLast, $endLast] = DashboardPeriod::previousWindow($period);
        $periodKey = DashboardPeriod::cacheKey($period, $start);
        $lastPeriodKey = match ($period) {
            'week' => $startLast->format('oW'),
            'quarter' => $startLast->format('Y').'Q'.$startLast->quarter,
            default => $startLast->format('Ym'),
        };

        $periodLabel = match ($period) {
            'week' => 'vs last week',
            'quarter' => 'vs last quarter',
            default => 'vs last month',
        };

        $periodScopeLabel = match ($period) {
            'week' => 'Week to date',
            'quarter' => 'Quarter to date',
            default => 'Month to date',
        };

        // last month
        $sales_last = 0;
        $qsales_last = 0;
        $start_last = $startLast;
        $end_last = $endLast;
        $monthKey = $periodKey;
        $lastMonthKey = $lastPeriodKey;

        if (auth()->user()->hasAnyPermission(['full dashboard'])) {

            $sales = Cache::remember(
                $this->dashboardCacheKey("sales.invoice.{$monthKey}"),
                self::DASHBOARD_CACHE_TTL,
                fn () => $this->billTotal('Invoice', $start, $end)
            );
            $sales_last = Cache::remember(
                $this->dashboardCacheKey("sales.invoice.{$lastPeriodKey}"),
                self::DASHBOARD_CACHE_TTL,
                fn () => $this->billTotal('Invoice', $startLast, $endLast)
            );
            $revenue = $sales;
            $revenueLast = $sales_last;

            $qsales = Cache::remember(
                $this->dashboardCacheKey("sales.quotation.{$periodKey}"),
                self::DASHBOARD_CACHE_TTL,
                fn () => $this->billTotal('Quotation', $start, $end)
            );
            $qsales_last = Cache::remember(
                $this->dashboardCacheKey("sales.quotation.{$lastPeriodKey}"),
                self::DASHBOARD_CACHE_TTL,
                fn () => $this->billTotal('Quotation', $startLast, $endLast)
            );

            $outstandingStats = Cache::remember(
                $this->dashboardCacheKey('outstanding.overdue'),
                self::DASHBOARD_CACHE_TTL,
                fn () => $this->invoiceOutstandingStats()
            );
            $outstanding = $outstandingStats['outstanding'];
            $overdueCount = $outstandingStats['overdueCount'];
            $overdueAmount = $outstandingStats['overdueAmount'];

            $revenueSeries = Cache::remember(
                $this->dashboardCacheKey("series.revenue.{$periodKey}"),
                self::DASHBOARD_CACHE_TTL,
                fn () => $this->revenueSeries($start, $end)
            );

        }

        // Jobs
        $job = Cache::remember(
            $this->dashboardCacheKey("jobs.count.{$periodKey}"),
            self::DASHBOARD_CACHE_TTL,
            fn () => WorkOrder::whereBetween('created_at', [$start, $end])->count()
        );
        $job_last = Cache::remember(
            $this->dashboardCacheKey("jobs.count.{$lastPeriodKey}"),
            self::DASHBOARD_CACHE_TTL,
            fn () => WorkOrder::whereBetween('created_at', [$startLast, $endLast])->count()
        );

        $works = $this->orderedPipelineWorks(Cache::remember(
            $this->dashboardCacheKey('works.status'),
            self::DASHBOARD_CACHE_TTL,
            fn () => DB::table('work_orders')
                ->select('status', DB::raw('count(*) as count'))
                ->groupBy('status')
                ->get()
        ));
        $pipelineWorks = $this->orderedPipelineWorks(Cache::remember(
            $this->dashboardCacheKey("works.pipeline.{$periodKey}"),
            self::DASHBOARD_CACHE_TTL,
            fn () => DB::table('work_orders')
                ->select('status', DB::raw('count(*) as count'))
                ->whereBetween('created_at', [$start, $end])
                ->groupBy('status')
                ->get()
        ));

        $activeStatuses = ['IN PROGRESS', 'CREATED', 'SUBMITTED', 'PENDING MOVE APPROVAL'];
        $inProduction = (int) $works->whereIn('status', $activeStatuses)->sum('count');

        $work_sections = Cache::remember(
            $this->dashboardCacheKey('works.department'),
            self::DASHBOARD_CACHE_TTL,
            fn () => DB::table('work_orders')
                ->select('department_id', DB::raw('count(*) as count'))
                ->groupBy('department_id')
                ->get()
        );

        // Single lookup shared by the stale/abandoned/pickup/department-stats sections —
        // replaces per-row Department::find() in the Blade loops.
        $departments = Cache::remember(
            $this->dashboardCacheKey('departments'),
            300,
            fn () => Department::all()->keyBy('id')
        );

        $staleJobs = Cache::remember(
            $this->dashboardCacheKey('stale.attention'),
            self::DASHBOARD_CACHE_TTL,
            fn () => WorkOrder::staleNeedsAttentionRows(8)
        );
        $abandonedJobsCount = Cache::remember(
            $this->dashboardCacheKey('stale.abandoned.count'),
            self::DASHBOARD_CACHE_TTL,
            fn () => WorkOrder::abandonedProductionCount()
        );
        $abandonedJobsPreview = Cache::remember(
            $this->dashboardCacheKey('stale.abandoned.preview'),
            self::DASHBOARD_CACHE_TTL,
            fn () => WorkOrder::abandonedProductionPreview(10)
        );
        $pickupJobs = Cache::remember(
            $this->dashboardCacheKey('pickup'),
            self::DASHBOARD_CACHE_TTL,
            fn () => WorkOrder::awaitingPickupRows()
        );
        $readyPickup = Cache::remember(
            $this->dashboardCacheKey('pickup.count'),
            self::DASHBOARD_CACHE_TTL,
            fn () => DB::table('work_orders')
                ->where('department_id', Department::PICKUP)
                ->whereIn('status', ['IN PROGRESS', 'SUBMITTED', 'CREATED'])
                ->count()
        );

        // products reorder
        $allLowStock = Cache::remember(
            $this->dashboardCacheKey('low_stock_products'),
            self::DASHBOARD_CACHE_TTL,
            function () {
                $warehouse = Session::get('warehouse');
                $materialProducts = Product::query()
                    ->where('type', 'Material')
                    ->where('inventory', true)
                    ->get(['id', 'name', 'reorder', 'type']);

                $balances = Product::stockBalancesFor($materialProducts->pluck('id'), $warehouse);

                return $materialProducts
                    ->filter(fn ($product) => ($balances[$product->id] ?? 0) <= $product->reorder)
                    ->values();
            }
        );
        $warehouse = Session::get('warehouse');
        Product::primeStockBalanceMemo(
            Product::stockBalancesFor($allLowStock->pluck('id'), $warehouse),
            $warehouse
        );
        $page = max(1, (int) request('page', 1));
        $perPage = 10;
        $products = new LengthAwarePaginator(
            $allLowStock->forPage($page, $perPage)->values(),
            $allLowStock->count(),
            $perPage,
            $page,
            ['path' => request()->url(), 'query' => request()->query()]
        );
        $product_counts = Cache::remember(
            $this->dashboardCacheKey('product_counts'),
            300,
            fn () => Product::where('type', 'Material')->count()
        );

        // Pending approvals for admins
        $pendingApprovals = 0;
        if (auth()->user()->can('approve job requests')) {
            $pendingApprovals = Cache::remember(
                $this->dashboardCacheKey('pending_approvals.'.auth()->id()),
                60,
                fn () => ApprovalRequest::pending()->count()
            );
        }

        $welcomeLinks = $this->welcomeLinksFor();

        return view('home', compact('product_counts', 'products', 'sales', 'qsales',
            'qbills', 'qsales_last', 'sales_last', 'start', 'end',
            'job', 'job_last', 'works', 'pipelineWorks', 'work_sections', 'pendingApprovals',
            'staleJobs', 'abandonedJobsCount', 'abandonedJobsPreview', 'pickupJobs', 'departments',
            'welcomeLinks', 'period', 'periodLabel', 'periodScopeLabel', 'revenue', 'revenueLast',
            'outstanding', 'overdueCount', 'overdueAmount', 'inProduction', 'readyPickup',
            'revenueSeries'));
    }

    /** @return list<array<string, mixed>> */
    private function welcomeLinksFor(): array
    {
        $user = auth()->user();
        if ($user === null) {
            return [];
        }

        $menu = app(BuildSidebarMenu::class)($user);

        return collect($menu['quickActions'])
            ->merge(collect($menu['groups'])->flatMap(fn ($group) => $group['items'] ?? []))
            ->reject(fn ($item) => ($item['id'] ?? null) === 'dashboard')
            ->take(8)
            ->values()
            ->all();
    }

    private function dashboardCacheKey(string $suffix): string
    {
        $entityId = auth()->user()->entity_id ?? 0;

        return "dashboard.{$entityId}.{$suffix}";
    }

    /** Active pipeline statuses first; terminal states trail for de-emphasis. */
    private function orderedPipelineWorks($works)
    {
        $order = array_flip([
            'CREATED',
            'IN PROGRESS',
            'SUBMITTED',
            'PENDING MOVE APPROVAL',
            'INVOICED',
            'FINISHED',
            'CANCELLED',
        ]);

        return $works->sortBy(fn ($row) => $order[$row->status] ?? 99)->values();
    }

    /**
     * Sum of Bill::amount() for bills of a type within a date window, computed
     * as a single aggregate query instead of loading bills and lazy-loading
     * each bill's items. Mirrors Bill::amount() exactly:
     * item_value = (price * quantity) - discount; total += item_value * (1 + tax/100).
     * COALESCE matches PHP's null-to-zero coercion; "/ 100.0" forces real
     * division on SQLite (the test database).
     */
    private function billTotal(string $type, Carbon $start, Carbon $end): float
    {
        return (float) Bill::where('bills.type', $type)
            ->whereBetween('bills.created_at', [$start, $end])
            ->join('bill_items', 'bill_items.bill_id', '=', 'bills.id')
            ->selectRaw('COALESCE('.$this->billAmountSumSql('bill_items').', 0) as total')
            ->value('total');
    }

    /**
     * Single source of truth for the per-bill amount SUM expression. Mirrors
     * Bill::amount(): item_value = (price * quantity) - discount; total +=
     * item_value * (1 + tax/100). COALESCE matches PHP's null-to-zero coercion;
     * "/ 100.0" forces real division on SQLite (the test database).
     *
     * @param  string  $table  bill_items table alias to qualify the columns with.
     */
    private function billAmountSumSql(string $table): string
    {
        return "SUM(({$table}.price * {$table}.quantity - COALESCE({$table}.discount, 0)) * (1 + COALESCE({$table}.tax, 0) / 100.0))";
    }

    /** @return array{outstanding: float, overdueCount: int, overdueAmount: float} */
    private function invoiceOutstandingStats(): array
    {
        $today = Carbon::today();

        $paymentsSub = DB::table('journals')
            ->join('accounts', 'accounts.id', '=', 'journals.account_id')
            ->whereIn('accounts.account_type', self::PAYMENT_ACCOUNT_TYPES)
            ->where('journals.debit', '>', 0)
            ->selectRaw('journals.bill_id, COALESCE(SUM(journals.debit), 0) as paid')
            ->groupBy('journals.bill_id');

        $amountSub = DB::table('bill_items')
            ->selectRaw('bill_id, COALESCE('.$this->billAmountSumSql('bill_items').', 0) as amount')
            ->groupBy('bill_id');

        $query = DB::table('bills')
            ->joinSub($amountSub, 'amounts', 'amounts.bill_id', '=', 'bills.id')
            ->leftJoinSub($paymentsSub, 'payments', 'payments.bill_id', '=', 'bills.id')
            ->where('bills.type', 'Invoice')
            ->selectRaw('amounts.amount - COALESCE(payments.paid, 0) as balance, bills.due_date');

        $user = auth()->user();
        if ($user && ! $user->hasRole(['Super Admin'])) {
            $query->where('bills.entity_id', $user->entity_id);
        }

        $outstanding = 0.0;
        $overdueCount = 0;
        $overdueAmount = 0.0;

        foreach ($query->get() as $row) {
            $balance = (float) $row->balance;
            if ($balance <= 0) {
                continue;
            }
            $outstanding += $balance;
            if ($row->due_date !== null && Carbon::parse($row->due_date)->startOfDay()->lte($today)) {
                $overdueCount++;
                $overdueAmount += $balance;
            }
        }

        return [
            'outstanding' => $outstanding,
            'overdueCount' => $overdueCount,
            'overdueAmount' => $overdueAmount,
        ];
    }

    /**
     * Daily invoice revenue across the window: ONE aggregate query grouped by
     * calendar date, then zero-filled to one point per day with display labels.
     * No entity scoping — mirrors billTotal(), which is intentionally unscoped.
     *
     * @return list<array{value: int, label: string, amount: string}>
     */
    private function revenueSeries(Carbon $start, Carbon $end): array
    {
        $byDate = Bill::where('bills.type', 'Invoice')
            ->whereBetween('bills.created_at', [$start, $end])
            ->join('bill_items', 'bill_items.bill_id', '=', 'bills.id')
            ->selectRaw('DATE(bills.created_at) as day, COALESCE('.$this->billAmountSumSql('bill_items').', 0) as total')
            ->groupBy('day')
            ->pluck('total', 'day');

        $points = [];
        $cursor = $start->copy()->startOfDay();
        $endDay = $end->copy()->startOfDay();
        while ($cursor <= $endDay) {
            $value = (int) round((float) ($byDate[$cursor->toDateString()] ?? 0));
            $points[] = [
                'value' => $value,
                'label' => $cursor->format('M j'),
                'amount' => 'USD '.shortMoney($value),
            ];
            $cursor->addDay();
        }

        return $points;
    }
}
