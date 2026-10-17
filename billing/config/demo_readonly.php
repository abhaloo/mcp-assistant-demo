<?php

use App\Http\Livewire\BillComponent;
use App\Http\Livewire\CustomerComponent;
use App\Http\Livewire\CustomerProfileComponent;
use App\Http\Livewire\CustomerSearchComponent;
use App\Http\Livewire\CustomerStatementComponent;
use App\Http\Livewire\Dashboard\LatestInvoicesComponent;
use App\Http\Livewire\Dashboard\LatestPurchasesComponent;
use App\Http\Livewire\JobSearchComponent;
use App\Http\Livewire\ProductSearchComponent;
use App\Http\Livewire\ProfileComponent;
use App\Http\Livewire\ShowBillComponent;
use App\Http\Livewire\WorkDataComponent;
use App\Http\Livewire\WorkOrderAttachmentComponent;
use App\Http\Livewire\WorkOrderComponent;

$paginationMethods = [
    'previousPage',
    'nextPage',
    'gotoPage',
    'resetPage',
    'setPage',
    'getPage',
];

return [
    BillComponent::class => array_merge([
        'mount',
        'setSortField',
        'setSort',
        'clearSort',
        'clearDateFilter',
        'updatingSearchKeyword',
        'updatingFilterDateFrom',
        'updatingFilterDateTo',
        'deleteConfirm',
    ], $paginationMethods),

    ShowBillComponent::class => [
        'mount',
        'billValue',
    ],

    WorkOrderComponent::class => array_merge([
        'mount',
        'onCustomerSelected',
        'onCustomerOrderSelected',
        'setDensity',
        'updatingSearchKeyword',
        'updatingPerPage',
        'setSortField',
        'setSort',
        'clearSort',
        'clearDateFilter',
        'clearBillFilter',
        'clearAllFilters',
        'openCopyModal',
        'resetCopyModal',
        'selectSuggestedOrder',
        'updatedCopyTarget',
        'updatedIsNewCustomer',
        'create',
        'editJob',
        'hasModalChanges',
        'deleteConfirm',
    ], $paginationMethods),

    WorkDataComponent::class => [
        'mount',
        'toggleGroup',
        'isGroupExpanded',
        'getFilledFieldCount',
        'locked',
        'checkBalance',
        'itemAdded',
        'itemDelete',
        'onCustomerSelected',
    ],

    WorkOrderAttachmentComponent::class => array_merge([
        'mount',
        'create',
        'download',
        'deleteConfirm',
    ], $paginationMethods),

    CustomerComponent::class => array_merge([
        'mount',
        'create',
        'update',
        'deleteConfirm',
    ], $paginationMethods),

    CustomerProfileComponent::class => array_merge([
        'mount',
    ], $paginationMethods),

    CustomerStatementComponent::class => [
        'mount',
        'filter',
    ],

    CustomerSearchComponent::class => [
        'mount',
        'resetFields',
        'selectCustomer',
        'closeDropdown',
        'updatedQuery',
    ],

    ProductSearchComponent::class => [
        'mount',
        'resetFields',
        'incrementHighlight',
        'decrementHighlight',
        'selectProduct',
        'updatedQuery',
    ],

    JobSearchComponent::class => [
        'mount',
        'resetFields',
        'selectJob',
        'clearSelection',
        'closeDropdown',
        'updatedQuery',
    ],

    ProfileComponent::class => [
        'mount',
    ],

    LatestInvoicesComponent::class => array_merge([
        'mount',
    ], $paginationMethods),

    LatestPurchasesComponent::class => array_merge([
        'mount',
    ], $paginationMethods),
];
