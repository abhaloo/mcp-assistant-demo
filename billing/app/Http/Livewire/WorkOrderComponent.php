<?php

namespace App\Http\Livewire;

use App\Models\ApprovalRequest;
use App\Models\Customer;
use App\Models\CustomerOrder;
use App\Models\Department;
use App\Models\Scopes\WorkOrderVisibilityScope;
use App\Models\Specification;
use App\Models\SpecificationItem;
use App\Models\WorkMessage;
use App\Models\WorkOrder;
use App\Services\OrderHistoryService;
use App\Support\ExceptionMessages;
use App\Support\LookupCache;
use Illuminate\Support\Facades\Log;
use Livewire\Attributes\On;
use Livewire\Component;
use Livewire\WithPagination;

class WorkOrderComponent extends Component
{
    use WithPagination;

    protected $paginationTheme = 'mcp';

    public $search_keyword = null;

    public $perPage = 10;

    public $isEditing = false;

    public $delete_confirm = null;

    public $work_order_id;

    public $status;

    public $department;

    public $remark;

    public $user = null;

    public $bill;

    public $customer;

    public $delivered = false;

    public $priority = 'normal';

    public $selected_work_order = null;

    // Original values for change tracking
    public $original_department = null;

    public $original_priority = 'normal';

    public $original_delivered = false;

    public string $density = 'expanded';

    /** @var 'index'|'customer'|'bill'|'none' */
    public string $contextVariant = 'index';

    protected $queryString = ['search_keyword', 'filter_status', 'filter_department', 'filter_date_from', 'filter_date_to', 'filter_bill_number', 'sort_field', 'sort_direction', 'perPage' => ['as' => 'per', 'except' => 10]];

    public $filter_department;

    public $filter_status = null;

    public $filter_date_from = null;

    public $filter_date_to = null;

    public $filter_bill_number = null;

    public $sort_field = 'created_at';

    public $sort_direction = 'desc';

    // Copy job modal properties
    public $copy_work_order_id = null;

    public $copy_target = 'new_order';

    public $copy_customer_id = null;

    public $copy_customer_order_id = null; // For attaching to existing order

    public $copy_source_job = null; // Holds the source job data for the modal

    public $copy_validation_error = false;

    public $is_new_customer = false;

    public $new_customer_name = null;

    public $new_customer_phone = null;

    public $new_customer_email = null;

    public $suggestedExistingOrders = [];

    public $copy_specification_id = null; // For selecting specification format when copying

    #[On('customerSelected')]
    public function onCustomerSelected($customerId)
    {
        $this->copy_customer_id = $customerId;
        $this->copy_validation_error = false;

        // Query for existing in-progress orders that have jobs for this customer
        // This handles cases where CustomerOrder.customer_id differs from WorkOrder.customer_id
        $this->suggestedExistingOrders = CustomerOrder::whereHas('workOrders', function ($query) use ($customerId) {
            $query->where('customer_id', $customerId);
        })
            ->whereNotIn('status', ['CANCELLED', 'FINISHED'])
            ->with('customer')
            ->withCount('workOrders')
            ->orderBy('created_at', 'desc')
            ->limit(3)
            ->get();
    }

    #[On('customerOrderSelected')]
    public function onCustomerOrderSelected($orderId, $customerId)
    {
        $this->copy_customer_order_id = $orderId;
        $this->copy_customer_id = $customerId;
        $this->copy_validation_error = false;
    }

    public function mount($bill = null, $customer = null, string $contextVariant = '')
    {
        $this->bill = $bill;
        $this->customer = is_numeric($customer)
            ? Customer::find($customer)
            : $customer;

        if ($contextVariant !== '') {
            $this->contextVariant = $contextVariant;
        } elseif ($bill) {
            $this->contextVariant = 'bill';
        } elseif ($this->customer) {
            $this->contextVariant = 'customer';
        } else {
            $this->contextVariant = 'index';
        }

        // Bill-scoped views always use compact (constrained embedded context).
        // Other contexts read the user's session preference.
        $this->density = $bill
            ? 'compact'
            : session('jobs.density', 'expanded');
    }

    public function setDensity(string $value): void
    {
        if (! in_array($value, ['expanded', 'compact'], true)) {
            return;
        }
        $this->density = $value;
        session(['jobs.density' => $value]);
    }

    public function updatingSearchKeyword()
    {
        $this->resetPage();
    }

    public function updatingPerPage()
    {
        $this->resetPage();
    }

    public function setSortField($field)
    {
        if ($this->sort_field === $field) {
            $this->sort_direction = $this->sort_direction === 'asc' ? 'desc' : 'asc';
        } else {
            $this->sort_field = $field;
            $this->sort_direction = 'desc';
        }
        $this->resetPage();
    }

    public function setSort($field, $direction)
    {
        $this->sort_field = $field;
        $this->sort_direction = $direction;
        $this->resetPage();
    }

    public function clearSort()
    {
        $this->sort_field = null;
        $this->sort_direction = null;
        $this->resetPage();
    }

    public function clearDateFilter()
    {
        $this->filter_date_from = null;
        $this->filter_date_to = null;
        $this->resetPage();
    }

    public function clearBillFilter()
    {
        $this->filter_bill_number = null;
        $this->resetPage();
    }

    public function clearAllFilters()
    {
        $this->filter_date_from = null;
        $this->filter_date_to = null;
        $this->filter_bill_number = null;
        $this->resetPage();
    }

    protected $rules = [
        'remark' => 'nullable',
    ];

    public function openCopyModal($workOrderId)
    {
        $workOrder = WorkOrder::with(['customerOrder', 'customer'])->find($workOrderId);

        if (! $workOrder) {
            $this->dispatch('swal:info', title: 'error', text: 'Job not found');

            return;
        }

        $this->copy_work_order_id = $workOrderId;
        $this->copy_source_job = $workOrder;
        $this->copy_customer_id = null; // Start with no customer selected
        $this->suggestedExistingOrders = [];

        // Default to 'New' specification, fallback to first available
        $newSpec = Specification::where('name', 'New')->first();
        $this->copy_specification_id = $newSpec ? $newSpec->id : Specification::first()?->id;

        // Always default to new order
        $this->copy_target = 'new_order';
    }

    public function resetCopyModal()
    {
        $this->reset(['copy_work_order_id', 'copy_target', 'copy_customer_id', 'copy_customer_order_id', 'copy_source_job', 'copy_validation_error', 'is_new_customer', 'new_customer_name', 'new_customer_phone', 'new_customer_email', 'copy_specification_id']);
        $this->suggestedExistingOrders = [];
        $this->dispatch('resetCustomerSearch');
        $this->dispatch('resetCustomerOrderSearch');
        $this->resetErrorBag();
    }

    public function selectSuggestedOrder($orderId)
    {
        $order = CustomerOrder::find($orderId);
        if ($order) {
            $this->copy_target = 'existing_order';
            $this->copy_customer_order_id = $orderId;
            $this->copy_customer_id = $order->customer_id;
            $this->copy_validation_error = false;
            $this->suggestedExistingOrders = [];
            $this->dispatch('setCustomerOrderSelection', $order->order_number, $order->customer->name ?? 'Customer');
        }
    }

    public function updatedCopyTarget()
    {
        // Reset selections when switching targets
        $this->copy_customer_id = null;
        $this->copy_customer_order_id = null;
        $this->copy_validation_error = false;
        $this->suggestedExistingOrders = [];
        $this->dispatch('resetCustomerSearch');
        $this->dispatch('resetCustomerOrderSearch');
    }

    public function updatedIsNewCustomer()
    {
        $this->reset(['copy_customer_id', 'new_customer_name', 'new_customer_phone', 'new_customer_email']);
        $this->suggestedExistingOrders = [];
        $this->resetErrorBag();
        $this->dispatch('resetCustomerSearch');
    }

    public function copyJob()
    {
        abort(403);
    }

    public function create()
    {
        $this->resetField();
    }

    public function store()
    {
        abort(403);
    }

    public function editJob($work_order_id)
    {
        $this->resetErrorBag();
        $this->isEditing = true;
        $this->work_order_id = $work_order_id;
        $wo = WorkOrder::with('department')->find($work_order_id);
        $this->selected_work_order = $wo;

        // Set current values
        $this->department = null; // Start with no selection to force user choice
        $this->remark = $wo->description;
        $this->priority = $wo->priority ?? 'normal';
        $this->delivered = false;

        // Store original values for change detection
        $this->original_department = $wo->department_id;
        $this->original_priority = $wo->priority ?? 'normal';
        $this->original_delivered = false;
    }

    public function hasModalChanges(): bool
    {
        // Department change (new selection made)
        if ($this->department && $this->department != $this->original_department) {
            return true;
        }

        // Priority change
        if ($this->priority !== $this->original_priority) {
            return true;
        }

        // Delivered toggle change
        if ($this->delivered !== $this->original_delivered) {
            return true;
        }

        return false;
    }

    public function deleteConfirm(WorkOrder $work_order)
    {
        $this->delete_confirm = $work_order;
    }

    public function destroy()
    {
        abort(403);
    }

    public function cancelMoveRequest($workOrderId)
    {
        abort(403);

        $workOrder = WorkOrder::find($workOrderId);

        if (! $workOrder) {
            $this->dispatch('swal:info', title: 'error', text: 'Work order not found.');

            return false;
        }

        $pendingApproval = $workOrder->pendingApproval(ApprovalRequest::TYPE_DEPARTMENT_MOVE);

        if (! $pendingApproval) {
            $this->dispatch('swal:info', title: 'error', text: 'No pending approval request found.');

            return false;
        }

        // Restore original status
        $previousStatus = $pendingApproval->payload['previous_status'] ?? 'IN PROGRESS';
        $workOrder->status = $previousStatus;
        $workOrder->save();

        // Delete the pending approval request
        $pendingApproval->delete();

        $this->dispatch('swal:info', title: 'success', text: 'Move request cancelled.');

        return true;
    }

    private function resetField()
    {
        $this->reset('status', 'department', 'work_order_id', 'isEditing', 'priority', 'selected_work_order', 'original_department', 'original_priority', 'original_delivered', 'delivered');
    }

    public function render()
    {
        if ($this->bill) {
            $work_orders = WorkOrder::query()
                ->withoutGlobalScope(WorkOrderVisibilityScope::class);
            WorkOrderVisibilityScope::applyToBuilder($work_orders, auth()->user());
            $work_orders->where('bill_id', $this->bill->id)->latest();
        } else {
            $work_orders = WorkOrder::query()->latest();
        }

        if ($this->customer) {
            $work_orders->where('customer_id', $this->customer->id);
        }

        if ($this->search_keyword) {
            $work_orders->where(function ($q) {
                $q->whereHas('customer', function ($q) {
                    $q->where('name', 'like', "%{$this->search_keyword}%");
                })->orWhere('title', 'like', "%{$this->search_keyword}%")
                    ->orWhere('id', $this->search_keyword)
                    ->orWhere('work_number', $this->search_keyword);
            });
        }

        if ($this->filter_status) {
            $work_orders->where('status', $this->filter_status);
        }

        if ($this->filter_department) {
            $work_orders->where('department_id', $this->filter_department);
        }

        // Date range filters
        if ($this->filter_date_from) {
            $work_orders->whereDate('created_at', '>=', $this->filter_date_from);
        }
        if ($this->filter_date_to) {
            $work_orders->whereDate('created_at', '<=', $this->filter_date_to);
        }

        // Bill number filter
        if ($this->filter_bill_number) {
            $searchTerm = $this->filter_bill_number;
            $work_orders->whereHas('bill', function ($q) use ($searchTerm) {
                $q->where('invoice_number', 'like', '%'.$searchTerm.'%')
                    ->orWhere('id', 'like', '%'.$searchTerm.'%');
            });
        }

        // Dynamic sorting
        if ($this->sort_field) {
            $work_orders->orderBy($this->sort_field, $this->sort_direction);
        } else {
            // Default sort:
            // 1. High Priority AND Active (not in dept 12, 13) - implicit "Active" check via department_id
            // 2. Latest created_at
            $work_orders->orderByRaw("CASE WHEN priority = 'high' AND department_id NOT IN (12, 13) THEN 1 ELSE 0 END DESC")
                ->latest();
        }

        // Eager load relationships to avoid N+1 queries
        $work_orders = $work_orders->with(['customer', 'department', 'bill', 'user', 'customerOrder'])->paginate($this->perPage);

        return view('livewire.work-order-component', [
            'work_orders' => $work_orders,
            'departments' => LookupCache::remember('departments', fn () => Department::all()),
            'specifications' => LookupCache::remember(
                'specifications',
                fn () => Specification::query()->orderBy('name')->get(['id', 'name'])
            ),
        ]);
    }
}
