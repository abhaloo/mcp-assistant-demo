@extends('layouts.app')

@section('content')
    @if(\Illuminate\Support\Facades\Gate::check('full dashboard') ||
\Illuminate\Support\Facades\Gate::check('inventory dashboard') ||
 \Illuminate\Support\Facades\Gate::check('job dashboard'))
    <div class="mcp mcp-dashboard">
        @php
            $nameParts = preg_split('/\s+/', trim(auth()->user()->name ?? ''), -1, PREG_SPLIT_NO_EMPTY);
            $firstName = $nameParts[0] ?? 'there';
            $greeting = now()->hour < 12 ? 'morning' : 'afternoon';
        @endphp
        <x-mcp.page-header>
            <x-slot:titleSlot>
                Good {{ $greeting }},
                <span class="mcp-page-header__title-soft">{{ $firstName }}</span>
            </x-slot:titleSlot>
            <x-slot:subtitleSlot>
                {{ now()->format('l, j M Y') }} · here is what needs you today.
            </x-slot:subtitleSlot>
            <x-slot:actions>
                @php
                    $periodParams = request()->except(['period', 'page', 'bills_page', 'inventories_page']);
                @endphp
                <div class="mcp-period" role="group" aria-label="Revenue period">
                    @foreach(['week' => 'Week', 'month' => 'Month', 'quarter' => 'Quarter'] as $periodKey => $periodTabLabel)
                        <a href="{{ route('home', array_merge($periodParams, ['period' => $periodKey])) }}"
                           @class(['is-active' => ($period ?? 'month') === $periodKey])
                           @if(($period ?? 'month') === $periodKey) aria-current="page" @endif>{{ $periodTabLabel }}</a>
                    @endforeach
                </div>
            </x-slot:actions>
        </x-mcp.page-header>

        @can('full dashboard')
            @php
                $revenueDiff = percentageDiff($revenue, $revenueLast);
                $revenueDeltaKind = $revenue > $revenueLast ? 'up' : ($revenue < $revenueLast ? 'down' : 'flat');
                $revenueDeltaWord = match ($revenueDeltaKind) {
                    'up' => 'up',
                    'down' => 'down',
                    default => 'unchanged',
                };
                $revenueNavLabel = 'Period revenue USD ' . shortMoney($revenue) . ', '
                    . $revenueDeltaWord . ' '
                    . str_replace('%', ' percent', $revenueDiff['label'])
                    . '. View revenue report.';
            @endphp
            <div class="row g-3 mb-4">
                <div class="col-12 col-sm-6 col-xl-3">
                    <x-mcp.kpi label="Period Revenue" icon="fa-coins" cur="USD"
                        :value="shortMoney($revenue)" :series="$revenueSeries"
                        spark="var(--mcp-success-dot)"
                        :sparkLabel="'Revenue trend, ' . strtolower($periodScopeLabel)"
                        :sparkDesc="'Daily invoice revenue for ' . strtolower($periodScopeLabel) . '.'"
                        :deltaKind="$revenueDeltaKind"
                        :deltaText="$revenueDiff['label']"
                        :hint="$periodScopeLabel"
                        :href="route('bill.index', ['type' => 'Invoice'])"
                        :interactive="true"
                        :navLabel="$revenueNavLabel" />
                </div>
                <div class="col-12 col-sm-6 col-xl-3">
                    <x-mcp.kpi label="Outstanding" icon="fa-file-invoice-dollar" cur="USD"
                        :value="shortMoney($outstanding)"
                        :deltaKind="$overdueCount > 0 ? 'down' : 'flat'" :deltaText="$overdueCount . ' overdue'"
                        :hint="shortMoney($overdueAmount) . ' past due'"
                        :href="route('bill.index')" />
                </div>
                <div class="col-12 col-sm-6 col-xl-3">
                    <x-mcp.kpi label="Jobs In Production" icon="fa-clock"
                        :value="number_format($inProduction)"
                        hint="Across active departments"
                        :href="route('work-order.index')" />
                </div>
                <div class="col-12 col-sm-6 col-xl-3">
                    <x-mcp.kpi label="Ready For Pickup" icon="fa-box-open"
                        :value="number_format($readyPickup)"
                        hint="Awaiting collection"
                        :href="route('work-order.index', ['filter_department' => \App\Models\Department::PICKUP])" />
                </div>
            </div>

            @php
                $canApprove = auth()->user()->can('approve job requests');
            @endphp
            @if($staleJobs->count() || ($canApprove && $pendingApprovals) || $overdueCount)
                <x-mcp.card title="Needs your attention" flush class="mb-4 mcp-card--feature">
                    <div class="mcp-attn-grid p-3">
                        @if($staleJobs->count())
                            <x-mcp.attention-card kind="crit" icon="fa-triangle-exclamation"
                                :count="$staleJobs->count()" label="Stale jobs — stuck 48h+"
                                :href="route('work-order.index')" />
                        @endif
                        @can('approve job requests')
                            @if($pendingApprovals)
                                <x-mcp.attention-card kind="warn" icon="fa-clipboard-check"
                                    :count="$pendingApprovals" label="Requests awaiting approval"
                                    :href="route('approvals.index')" />
                            @endif
                        @endcan
                        @if($overdueCount)
                            <x-mcp.attention-card kind="info" icon="fa-file-invoice"
                                :count="$overdueCount"
                                :label="'Overdue — ' . shortMoney($overdueAmount) . ' to chase'"
                                :href="route('bill.index')" />
                        @endif
                    </div>
                </x-mcp.card>
            @endif

            <x-mcp.card title="Production pipeline" flush class="mb-4">
                <x-slot:actions>
                    <x-mcp.btn variant="ghost" size="sm" :href="route('work-order.index')">View jobs</x-mcp.btn>
                </x-slot:actions>
                <div class="p-3"><x-mcp.pipeline :works="$pipelineWorks" /></div>
            </x-mcp.card>
        @endcan

        @canany(['full dashboard', 'job dashboard', 'view production alerts'])
            @cannot('full dashboard')
            @if($staleJobs->count() > 0)
                <div class="row mb-4">
                    <div class="col-12">
                        <x-mcp.card title="Stale production jobs" :count="$staleJobs->count()" flush class="mcp-card--accent-warning">
                            <x-slot:actions>
                                <x-mcp.btn variant="ghost" size="sm" :href="route('work-order.index')">All Jobs</x-mcp.btn>
                            </x-slot:actions>

                            <div class="d-none d-md-block mcp-table-wrap">
                                <table class="mcp-table">
                                    <thead>
                                    <tr>
                                        <th>Job</th>
                                        <th>Customer</th>
                                        <th>Department</th>
                                        <th class="mcp-td--right">Waiting</th>
                                    </tr>
                                    </thead>
                                    <tbody>
                                    @foreach($staleJobs as $sj)
                                        @php
                                            $h = $sj->hours_in_dept;
                                            $urgency = $h >= 336 ? 'critical' : ($h >= 168 ? 'elevated' : ($h >= 48 ? 'warn' : 'normal'));
                                            $timeStr = \App\Helpers\DurationHelper::compact((int) $h);
                                            $dept = $departments->get($sj->department_id);
                                        @endphp
                                        <tr class="mcp-row--link mcp-stale-{{ $urgency }}">
                                            <td class="mcp-cell-strong">
                                                <a href="{{ route('work-order.show', $sj->id) }}" class="mcp-row__stretch"
                                                    aria-label="View job {{ $sj->work_order_number ?? '#'.$sj->id }}"><span class="visually-hidden">View</span></a>
                                                @if($sj->priority === 'high')
                                                    <i class="fa fa-exclamation text-danger mcp-icon--xs"></i>
                                                @endif
                                                {{ $sj->work_order_number ?? '#'.$sj->id }}
                                            </td>
                                            <td><div class="mcp-truncate">{{ $sj->customer_name ?? '—' }}</div></td>
                                            <td><x-mcp.dept-badge :department="$dept" /></td>
                                            <td class="mcp-td--right mcp-td--mono">
                                                <span class="d-inline-flex align-items-center gap-1">
                                                    @if($urgency === 'critical')
                                                        <span class="mcp-stale-pulse-dot"></span>
                                                    @elseif($urgency !== 'normal')
                                                        <span class="mcp-stale-dot mcp-stale-dot-{{ $urgency }}"></span>
                                                    @endif
                                                    <span class="mcp-stale-time-{{ $urgency }}">{{ $timeStr }}</span>
                                                </span>
                                            </td>
                                        </tr>
                                    @endforeach
                                    </tbody>
                                </table>
                            </div>

                            <div class="d-md-none">
                                @foreach($staleJobs as $sj)
                                    @php
                                        $h = $sj->hours_in_dept;
                                        $urgency = $h >= 336 ? 'critical' : ($h >= 168 ? 'elevated' : ($h >= 48 ? 'warn' : 'normal'));
                                        $timeStr = \App\Helpers\DurationHelper::compact((int) $h);
                                        $dept = $departments->get($sj->department_id);
                                    @endphp
                                    <a href="{{ route('work-order.show', $sj->id) }}" class="mcp-list-row mcp-stale-{{ $urgency }}">
                                        <span class="mcp-list-row__main">
                                            <span class="mcp-list-row__title">{{ $sj->work_order_number ?? '#'.$sj->id }}
                                                <span class="fw-normal text-muted">— {{ \Illuminate\Support\Str::limit($sj->customer_name ?? '—', 22) }}</span>
                                            </span>
                                            <span class="mcp-list-row__sub d-block"><x-mcp.dept-badge :department="$dept" /></span>
                                        </span>
                                        <span class="mcp-list-row__end mcp-stale-time-{{ $urgency }}">
                                            @if($urgency === 'critical')<span class="mcp-stale-pulse-dot me-1"></span>@endif
                                            {{ $timeStr }}
                                        </span>
                                    </a>
                                @endforeach
                            </div>
                        </x-mcp.card>
                    </div>
                </div>
                @endif
            @endcannot
        @endcanany

        <div class="row">
            <div class="col-12 col-xl-8">
                <div class="row">
                    @can('full dashboard')
                        <div class="col-12 mb-4">
                            <x-mcp.card title="Latest Invoices" flush class="mcp-card--feature">
                                <x-slot:actions>
                                    <x-mcp.btn variant="ghost" size="sm" :href="route('bill.index')">See all</x-mcp.btn>
                                </x-slot:actions>

                                @livewire('dashboard.latest-invoices', ['period' => $period])
                            </x-mcp.card>
                        </div>

                        <div class="col-12 mb-4">
                            <x-mcp.card title="Latest Purchases" flush>
                                <x-slot:actions>
                                    <x-mcp.btn variant="ghost" size="sm" :href="route('inventory.index')">See all</x-mcp.btn>
                                </x-slot:actions>

                                @livewire('dashboard.latest-purchases', ['period' => $period])
                            </x-mcp.card>
                        </div>
                    @endcan

                    @canany(['full dashboard', 'job dashboard', 'view production alerts'])
                        @if($abandonedJobsCount > 0)
                            <div class="col-12 mb-4">
                                <x-mcp.card flush class="mcp-card--accent-neutral">
                                    <a class="d-flex align-items-center justify-content-between text-decoration-none px-3 py-2 border-bottom"
                                       data-coreui-toggle="collapse" href="#abandonedJobsList" role="button"
                                       aria-expanded="false" aria-controls="abandonedJobsList">
                                        <div class="d-flex align-items-center gap-2">
                                            <i class="fa fa-archive text-muted"></i>
                                            <span class="mcp-card__title mb-0">Likely Abandoned</span>
                                            <span class="mcp-card__count">{{ $abandonedJobsCount }}</span>
                                        </div>
                                        <i class="fa fa-chevron-down text-muted mcp-abandoned-chevron"></i>
                                    </a>

                                    <div class="collapse" id="abandonedJobsList">
                                        <div class="px-3 py-2 mcp-list-row__sub border-bottom">
                                            <i class="fa fa-info-circle me-1"></i>
                                            These jobs have had no activity for 3+ months. They may need to be closed or moved.
                                            @if($abandonedJobsCount > $abandonedJobsPreview->count())
                                                <span class="d-block mt-1">
                                                    Showing {{ $abandonedJobsPreview->count() }} of {{ $abandonedJobsCount }}.
                                                    <a href="{{ route('work-order.index') }}" class="text-decoration-none">View all jobs</a>
                                                </span>
                                            @endif
                                        </div>

                                        <div class="d-none d-md-block mcp-table-wrap">
                                            <table class="mcp-table">
                                                <thead>
                                                <tr>
                                                    <th>Job</th>
                                                    <th>Customer</th>
                                                    <th>Department</th>
                                                    <th>Status</th>
                                                    <th class="mcp-td--right">Inactive</th>
                                                </tr>
                                                </thead>
                                                <tbody>
                                                @foreach($abandonedJobsPreview as $aj)
                                                    @php
                                                        $ah = $aj->hours_in_dept;
                                                        $aTimeStr = \App\Helpers\DurationHelper::compact((int) $ah);
                                                        $aDept = $departments->get($aj->department_id);
                                                    @endphp
                                                    <tr class="mcp-row--link mcp-abandoned-row">
                                                        <td class="mcp-cell-strong">
                                                            <a href="{{ route('work-order.show', $aj->id) }}" class="mcp-row__stretch"
                                                                aria-label="View job {{ $aj->work_order_number ?? '#'.$aj->id }}"><span class="visually-hidden">View</span></a>
                                                            {{ $aj->work_order_number ?? '#'.$aj->id }}
                                                        </td>
                                                        <td><div class="mcp-truncate">{{ $aj->customer_name ?? '—' }}</div></td>
                                                        <td><x-mcp.dept-badge :department="$aDept" /></td>
                                                        <td>
                                                            <x-mcp.status :kind="\App\Models\WorkOrder::statusKindFor($aj->status)" size="sm" :label="\App\Models\WorkOrder::statusLabelFor($aj->status)" />
                                                        </td>
                                                        <td class="mcp-td--right mcp-td--mono mcp-td--secondary">{{ $aTimeStr }}</td>
                                                    </tr>
                                                @endforeach
                                                </tbody>
                                            </table>
                                        </div>

                                        <div class="d-md-none">
                                            @foreach($abandonedJobsPreview as $aj)
                                                @php
                                                    $ah = $aj->hours_in_dept;
                                                    $aTimeStr = \App\Helpers\DurationHelper::compact((int) $ah);
                                                    $aDept = $departments->get($aj->department_id);
                                                @endphp
                                                <a href="{{ route('work-order.show', $aj->id) }}" class="mcp-list-row mcp-abandoned-row">
                                                    <span class="mcp-list-row__main">
                                                        <span class="mcp-list-row__title">{{ $aj->work_order_number ?? '#'.$aj->id }}
                                                            <span class="fw-normal text-muted">— {{ \Illuminate\Support\Str::limit($aj->customer_name ?? '—', 22) }}</span>
                                                        </span>
                                                        <span class="mcp-list-row__sub d-block">
                                                            <x-mcp.dept-badge :department="$aDept" />
                                                            <x-mcp.status :kind="\App\Models\WorkOrder::statusKindFor($aj->status)" size="sm" :label="\App\Models\WorkOrder::statusLabelFor($aj->status)" />
                                                        </span>
                                                    </span>
                                                    <span class="mcp-list-row__end mcp-td--mono mcp-td--secondary">{{ $aTimeStr }}</span>
                                                </a>
                                            @endforeach
                                        </div>
                                    </div>
                                </x-mcp.card>
                            </div>
                        @endif

                        @if($pickupJobs->count() > 0)
                            <div class="col-12 mb-4">
                                <x-mcp.card title="Ready for Pickup" :count="$pickupJobs->count()" flush class="mcp-card--accent-success">
                                    <x-slot:actions>
                                        <x-mcp.btn variant="ghost" size="sm" :href="route('work-order.index', ['filter_department' => \App\Models\Department::PICKUP])">See all</x-mcp.btn>
                                    </x-slot:actions>

                                    <div class="d-none d-md-block mcp-table-wrap">
                                        <table class="mcp-table">
                                            <thead>
                                            <tr>
                                                <th>Customer</th>
                                                <th>Job</th>
                                                <th class="mcp-td--right">Waiting</th>
                                                <th class="mcp-td--right">Phone</th>
                                            </tr>
                                            </thead>
                                            <tbody>
                                            @foreach($pickupJobs as $pj)
                                                @php
                                                    $ph = $pj->hours_waiting;
                                                    $waitStr = \App\Helpers\DurationHelper::long((int) $ph);
                                                    $waitUrgency = $ph > 336 ? 'critical' : ($ph > 168 ? 'elevated' : ($ph > 72 ? 'warn' : 'normal'));
                                                @endphp
                                                <tr class="mcp-row--link">
                                                    <td class="mcp-cell-strong">
                                                        <a href="{{ route('work-order.show', $pj->id) }}" class="mcp-row__stretch"
                                                            aria-label="View pickup job for {{ $pj->customer_name ?? 'customer' }}"><span class="visually-hidden">View</span></a>
                                                        <div class="mcp-truncate">{{ \Illuminate\Support\Str::limit($pj->customer_name ?? '—', 28) }}</div>
                                                    </td>
                                                    <td>{{ \Illuminate\Support\Str::limit($pj->title ?? $pj->work_order_number ?? '#'.$pj->id, 28) }}</td>
                                                    <td class="mcp-td--right mcp-td--mono">
                                                        <span class="mcp-stale-time-{{ $waitUrgency }}">{{ $waitStr }}</span>
                                                    </td>
                                                    <td class="mcp-td--right mcp-td--mute">{{ $pj->customer_phone ?? '—' }}</td>
                                                </tr>
                                            @endforeach
                                            </tbody>
                                        </table>
                                    </div>

                                    <div class="d-md-none">
                                        @foreach($pickupJobs as $pj)
                                            @php
                                                $ph = $pj->hours_waiting;
                                                $waitStr = \App\Helpers\DurationHelper::long((int) $ph);
                                                $waitUrgency = $ph > 336 ? 'critical' : ($ph > 168 ? 'elevated' : ($ph > 72 ? 'warn' : 'normal'));
                                            @endphp
                                            <a href="{{ route('work-order.show', $pj->id) }}" class="mcp-list-row">
                                                <span class="mcp-list-row__main">
                                                    <span class="mcp-list-row__title">{{ \Illuminate\Support\Str::limit($pj->customer_name ?? '—', 22) }}</span>
                                                    <span class="mcp-list-row__sub d-block">{{ \Illuminate\Support\Str::limit($pj->title ?? $pj->work_order_number ?? '', 30) }}</span>
                                                </span>
                                                <span class="mcp-list-row__end">
                                                    <span class="mcp-stale-time-{{ $waitUrgency }} d-block">{{ $waitStr }}</span>
                                                    @if($pj->customer_phone)
                                                        <span class="mcp-list-row__sub d-block">{{ $pj->customer_phone }}</span>
                                                    @endif
                                                </span>
                                            </a>
                                        @endforeach
                                    </div>
                                </x-mcp.card>
                            </div>
                        @endif
                    @endcanany
                </div>
            </div>
            <div class="col-12 col-xl-4">
                @can('approve job requests')
                    @if($pendingApprovals > 0)
                        @cannot('full dashboard')
                        <div class="col-12 px-0 mb-4">
                            <x-mcp.card class="mcp-card--pulse">
                                <div class="text-center py-4">
                                    <div class="h6 fw-bold text-warning mb-2">
                                        <i class="fa fa-exclamation-triangle"></i> Pending Approvals
                                    </div>
                                    <div class="mcp-stat__value text-warning">{{ $pendingApprovals }}</div>
                                    <p class="text-muted mb-3">request{{ $pendingApprovals > 1 ? 's' : '' }} awaiting review</p>
                                    <x-mcp.btn variant="warning" size="sm" icon="fa-eye" :href="route('approvals.index')">Review Now</x-mcp.btn>
                                </div>
                            </x-mcp.card>
                        </div>
                        @endcannot
                    @endif
                @endcan

                @canany(['full dashboard', 'job dashboard'])
                    @cannot('full dashboard')
                    <div class="col-12 px-0 mb-4">
                        <x-mcp.card title="Jobs Status" flush>
                            <x-slot:actions>
                                <x-mcp.btn variant="ghost" size="sm" :href="route('work-order.index')">See all</x-mcp.btn>
                            </x-slot:actions>

                            <div class="mcp-table-wrap">
                                <table class="mcp-table">
                                    <thead>
                                    <tr>
                                        <th>Status</th>
                                        <th class="mcp-td--right">Number of Jobs</th>
                                    </tr>
                                    </thead>
                                    <tbody>
                                    @forelse($works as $work)
                                        <tr>
                                            <td>
                                                <x-mcp.status :kind="\App\Models\WorkOrder::statusKindFor($work->status)" size="sm" :label="\App\Models\WorkOrder::statusLabelFor($work->status)" />
                                            </td>
                                            <td class="mcp-td--right mcp-td--mono"><a href="{{ route('work-order.index', ['filter_status' => $work->status]) }}">{{ $work->count }}</a></td>
                                        </tr>
                                    @empty
                                        <tr>
                                            <td colspan="2">
                                                <x-mcp.empty title="No jobs yet"
                                                    hint="Production jobs will appear here grouped by status.">
                                                    <x-slot:action>
                                                        <x-mcp.btn variant="primary" size="sm" icon="fa-clock" :href="route('work-order.index')">View jobs</x-mcp.btn>
                                                    </x-slot:action>
                                                </x-mcp.empty>
                                            </td>
                                        </tr>
                                    @endforelse
                                    </tbody>
                                </table>
                            </div>
                        </x-mcp.card>
                    </div>
                    @endcannot
                    <div class="col-12 px-0 mb-4">
                        <x-mcp.card title="Jobs Department" flush>
                            <x-slot:actions>
                                <x-mcp.btn variant="ghost" size="sm" :href="route('work-order.index')">See all</x-mcp.btn>
                            </x-slot:actions>

                            @php $deptMax = $work_sections->max('count') ?: 1; @endphp
                            <div class="px-3 pb-2">
                                @forelse($work_sections as $work)
                                    @php $dept = $departments->get($work->department_id); @endphp
                                    <x-mcp.bar-row
                                        :label="$dept->name ?? 'Unknown'"
                                        :count="$work->count"
                                        :max="$deptMax"
                                        :href="route('work-order.index', ['filter_department' => $work->department_id])" />
                                @empty
                                    <x-mcp.empty title="No jobs by department"
                                        hint="Jobs will appear here once they are assigned to a department." />
                                @endforelse
                            </div>
                        </x-mcp.card>
                    </div>
                @endcanany

                @can('approve job requests')
                    @if($pendingApprovals == 0)
                        @cannot('full dashboard')
                        <div class="col-12 px-0 mb-4">
                            <x-mcp.card title="Pending Approvals">
                                <x-slot:actions>
                                    <x-mcp.btn variant="ghost" size="sm" :href="route('approvals.index')">View All</x-mcp.btn>
                                </x-slot:actions>
                                <div class="text-center py-4">
                                    <x-mcp.empty title="No pending requests" />
                                </div>
                            </x-mcp.card>
                        </div>
                        @endcannot
                    @endif
                @endcan

                @canany(['full dashboard', 'inventory dashboard'])
                    <div class="col-12 px-0 mb-4">
                        <x-mcp.card title="Low Stock Materials" :count="$products->total()" flush>
                            <x-slot:actions>
                                <x-mcp.btn variant="ghost" size="sm" :href="route('product.index')">See all</x-mcp.btn>
                            </x-slot:actions>

                            <p class="mcp-list-row__sub text-center pt-2">Materials at or below reorder level</p>
                            <div class="mcp-table-wrap">
                                <table class="mcp-table">
                                    <thead>
                                    <tr>
                                        <th>Product</th>
                                        <th class="mcp-td--right">Stock In Hands</th>
                                    </tr>
                                    </thead>
                                    <tbody>
                                    @forelse($products as $product)
                                        <tr>
                                            <td class="mcp-cell-strong">{{ $product->name }}</td>
                                            <td class="mcp-td--right mcp-td--mono">{{ $product->stockBalance() }}</td>
                                        </tr>
                                    @empty
                                        <tr>
                                            <td colspan="2">
                                                <x-mcp.empty title="No low-stock materials"
                                                    hint="All materials are above their reorder thresholds." />
                                            </td>
                                        </tr>
                                    @endforelse
                                    </tbody>
                                </table>
                            </div>
                            @if ($products->hasMorePages())
                                <div class="mcp-card__footer">
                                    <x-mcp.btn variant="ghost" size="sm" :href="$products->nextPageUrl()">Show more</x-mcp.btn>
                                </div>
                            @endif
                        </x-mcp.card>
                    </div>
                @endcanany
            </div>
        </div>
    </div>
    @else
        <div class="mcp mcp-dashboard">
            <x-mcp.page-header>
                <x-slot:titleSlot>
                    <span class="mcp-page-header__title-soft">Welcome</span> Back!
                </x-slot:titleSlot>
                <x-slot:subtitleSlot>
                    Pick up where you left off — shortcuts to your permitted areas.
                </x-slot:subtitleSlot>
            </x-mcp.page-header>

            @if(count($welcomeLinks) > 0)
                <div class="mcp-welcome">
                    <div class="row g-3">
                        @foreach($welcomeLinks as $link)
                            <div class="col-6 col-md-4 col-xl-3">
                                <a href="{{ $link['href'] }}" class="mcp-welcome-link">
                                    <i class="fa {{ $link['icon'] }}"></i>
                                    <span>{{ $link['label'] }}</span>
                                </a>
                            </div>
                        @endforeach
                    </div>
                </div>
            @else
                <x-mcp.card>
                    <x-mcp.empty title="No modules assigned yet"
                        hint="Contact an administrator to grant access to orders, jobs, inventory, or reports." />
                </x-mcp.card>
            @endif
        </div>
    @endif
@endsection
