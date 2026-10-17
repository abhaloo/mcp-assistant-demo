@extends('layouts.app')

@section('content')
    <div class="row">
        <div class="col">
            <h1 class="h3 mb-3"><strong>Admin</strong> Tools</h1>
            <p class="text-muted mb-4">Access administrative settings and configuration tools</p>
        </div>
    </div>

    @php
        $hasAnyPermission = false;
    @endphp

    {{-- Configuration Tools Section --}}
    <h5 class="text-uppercase text-muted mb-3">Configuration</h5>
    <div class="row mb-4">
        @can('view vat')
            @php $hasAnyPermission = true; @endphp
            <div class="col-12 col-sm-6 col-xl-4 mb-4">
                <a href="{{ route('vats') }}" class="text-decoration-none">
                    <div class="card border-0 shadow h-100" style="cursor: pointer; transition: transform 0.2s;">
                        <div class="card-body">
                            <div class="d-flex align-items-center">
                                <div class="icon-shape rounded me-3" style="width: 50px; height: 50px; display: flex; align-items: center; justify-content: center; background-color: #e8f5e9;">
                                    <i class="fa fa-percent text-success" style="font-size: 24px;"></i>
                                </div>
                                <div>
                                    <h5 class="mb-1 text-dark">VAT Rates</h5>
                                    <p class="text-muted mb-0 small">Manage tax rates and VAT configurations</p>
                                </div>
                            </div>
                        </div>
                    </div>
                </a>
            </div>
        @endcan

        @can('view specification')
            @php $hasAnyPermission = true; @endphp
            <div class="col-12 col-sm-6 col-xl-4 mb-4">
                <a href="{{ route('specification.index') }}" class="text-decoration-none">
                    <div class="card border-0 shadow h-100" style="cursor: pointer; transition: transform 0.2s;">
                        <div class="card-body">
                            <div class="d-flex align-items-center">
                                <div class="icon-shape rounded me-3" style="width: 50px; height: 50px; display: flex; align-items: center; justify-content: center; background-color: #e3f2fd;">
                                    <i class="fa fa-list-alt text-primary" style="font-size: 24px;"></i>
                                </div>
                                <div>
                                    <h5 class="mb-1 text-dark">Specifications</h5>
                                    <p class="text-muted mb-0 small">Product specification templates</p>
                                </div>
                            </div>
                        </div>
                    </div>
                </a>
            </div>
        @endcan

        @can('view category')
            @php $hasAnyPermission = true; @endphp
            <div class="col-12 col-sm-6 col-xl-4 mb-4">
                <a href="{{ route('category.index') }}" class="text-decoration-none">
                    <div class="card border-0 shadow h-100" style="cursor: pointer; transition: transform 0.2s;">
                        <div class="card-body">
                            <div class="d-flex align-items-center">
                                <div class="icon-shape rounded me-3" style="width: 50px; height: 50px; display: flex; align-items: center; justify-content: center; background-color: #fff3e0;">
                                    <i class="fa fa-folder text-warning" style="font-size: 24px;"></i>
                                </div>
                                <div>
                                    <h5 class="mb-1 text-dark">Categories</h5>
                                    <p class="text-muted mb-0 small">Product category management</p>
                                </div>
                            </div>
                        </div>
                    </div>
                </a>
            </div>
        @endcan

        @can('view unit')
            @php $hasAnyPermission = true; @endphp
            <div class="col-12 col-sm-6 col-xl-4 mb-4">
                <a href="{{ route('unit.index') }}" class="text-decoration-none">
                    <div class="card border-0 shadow h-100" style="cursor: pointer; transition: transform 0.2s;">
                        <div class="card-body">
                            <div class="d-flex align-items-center">
                                <div class="icon-shape rounded me-3" style="width: 50px; height: 50px; display: flex; align-items: center; justify-content: center; background-color: #f3e5f5;">
                                    <i class="fa fa-balance-scale text-purple" style="font-size: 24px; color: #9c27b0;"></i>
                                </div>
                                <div>
                                    <h5 class="mb-1 text-dark">Units</h5>
                                    <p class="text-muted mb-0 small">Units of measurement</p>
                                </div>
                            </div>
                        </div>
                    </div>
                </a>
            </div>
        @endcan
    </div>

    {{-- Reports Section --}}
    <h5 class="text-uppercase text-muted mb-3">Reports</h5>
    <div class="row mb-4">
        @can('summary')
            @php $hasAnyPermission = true; @endphp
            <div class="col-12 col-sm-6 col-xl-4 mb-4">
                <a href="{{ route('report.summary') }}" class="text-decoration-none">
                    <div class="card border-0 shadow h-100" style="cursor: pointer; transition: transform 0.2s;">
                        <div class="card-body">
                            <div class="d-flex align-items-center">
                                <div class="icon-shape rounded me-3" style="width: 50px; height: 50px; display: flex; align-items: center; justify-content: center; background-color: #e8eaf6;">
                                    <i class="fa fa-chart-pie text-info" style="font-size: 24px;"></i>
                                </div>
                                <div>
                                    <h5 class="mb-1 text-dark">Summary Report</h5>
                                    <p class="text-muted mb-0 small">Aggregated business summaries</p>
                                </div>
                            </div>
                        </div>
                    </div>
                </a>
            </div>
        @endcan

        @can('cash flow statement')
            @php $hasAnyPermission = true; @endphp
            <div class="col-12 col-sm-6 col-xl-4 mb-4">
                <a href="{{ route('cash-flow-statement') }}" class="text-decoration-none">
                    <div class="card border-0 shadow h-100" style="cursor: pointer; transition: transform 0.2s;">
                        <div class="card-body">
                            <div class="d-flex align-items-center">
                                <div class="icon-shape rounded me-3" style="width: 50px; height: 50px; display: flex; align-items: center; justify-content: center; background-color: #e0f2f1;">
                                    <i class="fa fa-money-bill-wave text-success" style="font-size: 24px;"></i>
                                </div>
                                <div>
                                    <h5 class="mb-1 text-dark">Cash Flow Statement</h5>
                                    <p class="text-muted mb-0 small">Cash inflows and outflows</p>
                                </div>
                            </div>
                        </div>
                    </div>
                </a>
            </div>
        @endcan
    </div>

    {{-- Data Tools Section --}}
    <h5 class="text-uppercase text-muted mb-3">Data Tools</h5>
    <div class="row mb-4">
        @can('view admin tools')
            @php $hasAnyPermission = true; @endphp
            <div class="col-12 col-sm-6 col-xl-4 mb-4">
                <a href="{{ route('orphan-linking') }}" class="text-decoration-none">
                    <div class="card border-0 shadow h-100" style="cursor: pointer; transition: transform 0.2s; border-left: 4px solid #ff9800 !important;">
                        <div class="card-body">
                            <div class="d-flex align-items-center">
                                <div class="icon-shape rounded me-3" style="width: 50px; height: 50px; display: flex; align-items: center; justify-content: center; background-color: #fff8e1;">
                                    <i class="fa fa-link text-warning" style="font-size: 24px;"></i>
                                </div>
                                <div>
                                    <h5 class="mb-1 text-dark">Data Cleanup</h5>
                                    <p class="text-muted mb-0 small">Link orphaned jobs to invoices</p>
                                </div>
                            </div>
                            <div class="mt-2">
                                <x-badge color="warning">Admin Tool</x-badge>
                            </div>
                        </div>
                    </div>
                </a>
            </div>
        @endcan

    </div>

    {{-- Empty state if no permissions --}}
    @if(!$hasAnyPermission)
        <div class="row">
            <div class="col-12">
                <div class="card border-0 shadow">
                    <div class="card-body text-center py-5">
                        <i class="fa fa-lock text-muted fa-3x mb-3"></i>
                        <h5 class="text-muted">No Admin Tools Available</h5>
                        <p class="text-muted mb-0">You don't have permission to access any admin tools.</p>
                    </div>
                </div>
            </div>
        </div>
    @endif
@endsection

@push('styles')
<style>
    .card:hover {
        transform: translateY(-2px);
        box-shadow: 0 0.5rem 1rem rgba(0, 0, 0, 0.15) !important;
    }
</style>
@endpush
