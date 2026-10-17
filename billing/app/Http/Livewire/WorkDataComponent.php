<?php

namespace App\Http\Livewire;

use App\Models\ApprovalRequest;
use App\Models\Customer;
use App\Models\Department;
use App\Models\Inventory;
use App\Models\InventoryItem;
use App\Models\Product;
use App\Models\WareHouse;
use App\Models\WorkMessage;
use App\Models\WorkOrder;
use App\Models\WorkOrderItem;
use App\Services\OrderHistoryService;
use Illuminate\Support\Facades\Session;
use Livewire\Attributes\On;
use Livewire\Component;

class WorkDataComponent extends Component
{
    public $workOrder;

    public $description;

    public $ordered_qty;

    public $no_pages;

    public $title;

    public $new_customer;

    public $payload = [];

    public $work_items = [];

    public $warehouse_id = 1;

    public $work_number = null;

    public $priority = 'normal';

    public $expandedGroups = [];

    public $mobile = false;

    // Job move
    public $department;

    public $remark = null;

    public $delivered = false;

    public function mount($workOrder, $mobile = false)
    {
        $this->mobile = $mobile;
        $this->workOrder = $workOrder;
        $this->payload = json_decode($workOrder->specifications, true);
        $this->ordered_qty = $workOrder->ordered_qty;
        $this->title = $workOrder->title;
        $this->no_pages = $workOrder->no_pages;
        $this->no_pages = $workOrder->no_pages;
        $this->description = $workOrder->description;
        $this->work_number = $workOrder->work_number ?? $workOrder->id;
        $this->priority = $workOrder->priority ?? 'normal';

        if ($this->workOrder->items) {
            foreach ($workOrder->items as $item) {
                $this->work_items[$item->product_id] = [
                    'id' => $item->product_id,
                    'name' => $item->product->name,
                    'cost' => $item->product->avarageCost(),
                    'unit' => $item->product->unit->name,
                    'quantity' => $item->quantity,
                ];
            }
        }

        // Auto-expand all groups by default
        $this->autoExpandAllGroups();
    }

    /**
     * Toggle a group's expanded/collapsed state
     */
    public function toggleGroup($groupName)
    {
        if (in_array($groupName, $this->expandedGroups)) {
            $this->expandedGroups = array_values(array_diff($this->expandedGroups, [$groupName]));
        } else {
            $this->expandedGroups[] = $groupName;
        }
    }

    /**
     * Check if a group is currently expanded
     */
    public function isGroupExpanded($groupName)
    {
        return in_array($groupName, $this->expandedGroups);
    }

    /**
     * Count how many fields in a group have values
     */
    public function getFilledFieldCount($groupName)
    {
        $count = 0;
        $items = $this->workOrder->specification->items
            ->where('group_name', $groupName);

        foreach ($items as $item) {
            $value = $this->payload[$item->name] ?? null;
            if (! empty($value) && $value !== [] && $value !== '') {
                // For checkbox arrays, check if any are checked
                if (is_array($value)) {
                    foreach ($value as $v) {
                        if ($v) {
                            $count++;
                            break;
                        }
                    }
                } else {
                    $count++;
                }
            }
        }

        return $count;
    }

    /**
     * Auto-expand groups - all when editing, only filled ones when submitted
     */
    private function autoExpandAllGroups()
    {
        $groupNames = $this->workOrder->specification->items
            ->whereNotNull('group_name')
            ->pluck('group_name')
            ->unique();

        // When submitted (editing CLOSED), only expand groups with filled fields
        if ($this->workOrder->editing == 'CLOSED') {
            $this->expandedGroups = $groupNames->filter(function ($groupName) {
                return $this->getFilledFieldCount($groupName) > 0;
            })->values()->toArray();
        } else {
            // When editing, expand all groups
            $this->expandedGroups = $groupNames->values()->toArray();
        }
    }

    public function locked($item)
    {

        $this->payload[$item] = ['locked' => 'true'];
    }

    public function checkBalance($item_id)
    {
        $item = $this->work_items[$item_id];
        Session::put('warehouse', $this->warehouse_id);
        $product = Product::find($item_id);

        if ($product->stockBalance() < $item['quantity']) {
            Session::remove('warehouse');
            $this->dispatch('swal:info', title: 'error', text: 'We only have '.$product->stockBalance().' left');
            $this->work_items[$item_id]['quantity'] = $product->stockBalance();

            return false;
        }

        return true;
    }

    #[On('itemAdded')]
    public function itemAdded($item_id)
    {

        Session::put('warehouse', $this->warehouse_id);
        $item = Product::find($item_id);

        $balance = $item->stockBalance();
        Session::remove('warehouse');

        $unscoped_item = Product::find($item_id); // load for all warehouses

        if ($item && $unscoped_item) {

            // if exist just add 1 to quantity
            if (array_key_exists($item_id, $this->work_items)) {
                $this->work_items[$item_id]['quantity']++;
            } else {

                $cost = $unscoped_item->avarageCost();

                if ($balance < ($this->work_items[$item_id]['quantity'] ?? 1)) {
                    $this->dispatch('swal:info', title: 'error', text: 'No Balance In Stock');

                    return false;
                }

                $this->work_items[$item_id] = [
                    'id' => $item->id,
                    'name' => $item->name,
                    'cost' => $cost,
                    'unit' => $item->unit->name,
                    'quantity' => 1,
                ];

            }

        }

    }

    public function itemDelete($item_id)
    {
        unset($this->work_items[$item_id]);
    }

    public function submit()
    {
        abort(403);
    }

    public function edit()
    {
        abort(403);
        $this->workOrder->editing = 'OPEN';
        $this->workOrder->save();
        $this->dispatch('swal:info', title: 'success', text: 'Job Open for edit');
    }

    public function store()
    {
        abort(403);
        //            $this->dispatch('swal:info', title: 'error', text: 'You Must Add Materials');
        //            return;
        //        }

        $this->workOrder = WorkOrder::updateOrCreate(
            [
                'id' => $this->workOrder->id,
            ], [
                'specifications' => json_encode($this->payload),
                'description' => $this->description,
                'ordered_qty' => $this->ordered_qty,
                'no_pages' => $this->no_pages,
                'title' => $this->title,
                'work_number' => $this->work_number,
                'priority' => $this->priority,
            ]);

        // clear previous items
        if (! empty($this->work_items)) {
            WorkOrderItem::where('work_order_id', $this->workOrder->id)->delete();
            foreach ($this->work_items as $item) {
                if ($item['quantity'] > 0 && $this->checkBalance($item['id'])) {
                    WorkOrderItem::updateOrCreate([
                        'work_order_id' => $this->workOrder->id,
                        'product_id' => $item['id'],
                    ], [
                        'quantity' => $item['quantity'],
                    ]);
                } else {
                    $this->work_items = [];
                }
            }
        }

        $this->dispatch('swal:info', title: 'success', text: 'Job Info. Saved');
        // close modal
        $this->dispatch('closeMaterialModal');
    }

    public function move()
    {
        abort(403);
        $this->validate([
            'department' => 'required_if:delivered,true',
            'remark' => 'required',
        ]);

        // Check if moved to same section
        $work_order = $this->workOrder;

        // If no department selected, just update priority and remark
        if (! $this->department) {
            $work_order->priority = $this->priority;
            // $work_order->description = $this->remark; // Maybe update description/remark? existing logic didn't imply updating order description from move remark, move remark is for message logged in WorkMessage.
            // But we should probably log a message that priority changed?
            $work_order->save();

            WorkMessage::create([
                'work_order_id' => $work_order->id,
                'section' => $work_order->department->name,
                'status' => $work_order->status,
                'message' => 'Priority updated to '.ucfirst($this->priority).'. '.$this->remark,
                'assigned_by' => auth()->user()->name,
            ]);

            $this->dispatch('swal:info', title: 'success', text: 'Job Priority Updated');
            $this->dispatch('closeMoveModal');

            return true;
        }

        $targetDepartment = Department::find($this->department);

        if (! $targetDepartment) {
            $this->dispatch('swal:info', title: 'error', text: 'Invalid Department');

            return false;
        }

        // Check if target department requires approval
        if ($targetDepartment->requires_approval) {
            // Check if there's already a pending approval request
            if ($work_order->hasPendingApproval(ApprovalRequest::TYPE_DEPARTMENT_MOVE)) {
                $this->dispatch('swal:info', title: 'info', text: 'An approval request is already pending for this job.');

                return false;
            }

            // Create approval request instead of executing directly
            try {
                $work_order->requestDepartmentMoveApproval(
                    [
                        'from_department_id' => $work_order->department_id,
                        'to_department_id' => $this->department,
                        'mark_delivered' => $this->delivered,
                        'remark' => $this->remark,
                        'previous_status' => $work_order->status,
                    ],
                    $this->remark
                );

                $this->dispatch('swal:info', title: 'success', text: 'Move to "'.$targetDepartment->name.'" requires approval. Request submitted for review.');

                return redirect(request()->header('Referer'));

            } catch (\Exception $e) {
                $this->dispatch('swal:info', title: 'error', text: $e->getMessage());

                return false;
            }
        }

        // No approval needed - execute immediately (existing logic)
        $prev_department = $work_order->department->name;

        if ($this->delivered) {
            $work_order->status = 'FINISHED';
            $work_order->department_id = $this->department;
        } else {
            $work_order->status = 'IN PROGRESS';
            $work_order->department_id = $this->department;
        }

        $section = $prev_department.' <i class="fa fa-arrow-right"></i> '.
            $targetDepartment->name;

        // add Message
        WorkMessage::create([
            'work_order_id' => $work_order->id,
            'section' => $section,
            'status' => $this->delivered ? 'DELIVERED' : 'FINISHED',
            'message' => $this->remark,
            'assigned_by' => auth()->user()->name,
        ]);

        $work_order->save();

        // Log to order history
        OrderHistoryService::logJobDepartmentChanged(
            $work_order,
            $prev_department,
            Department::find($this->department)->name
        );

        $this->dispatch('swal:info', title: 'success', text: $this->workOrder->id ? 'Job Updated.' : 'Job Created');

        // close modal
        $this->dispatch('closeMoveModal');

        return true;
    }

    public function cancelMoveRequest()
    {
        abort(403);
        $pendingApproval = $this->workOrder->pendingApproval(ApprovalRequest::TYPE_DEPARTMENT_MOVE);

        if (! $pendingApproval) {
            $this->dispatch('swal:info', title: 'error', text: 'No pending approval request found.');

            return false;
        }

        // Restore original status
        $previousStatus = $pendingApproval->payload['previous_status'] ?? 'IN PROGRESS';
        $this->workOrder->status = $previousStatus;
        $this->workOrder->save();

        // Delete the pending approval request
        $pendingApproval->delete();

        $this->dispatch('swal:info', title: 'success', text: 'Move request cancelled.');

        return redirect(request()->header('Referer'));
    }

    public function approveRequest()
    {
        abort(403);
        abort_unless(auth()->user()->can('approve job requests'), 403);

        $pendingApproval = $this->workOrder->pendingApproval(ApprovalRequest::TYPE_DEPARTMENT_MOVE);

        if (! $pendingApproval || ! $pendingApproval->isPending()) {
            $this->dispatch('swal:info', title: 'error', text: 'No pending approval request found or request already processed.');

            return false;
        }

        $result = $pendingApproval->approve(auth()->user(), null);

        if ($result) {
            $this->dispatch('swal:info', title: 'success', text: 'Request approved successfully.');

            return redirect(request()->header('Referer'));
        } else {
            $this->dispatch('swal:info', title: 'error', text: 'Failed to approve request.');

            return false;
        }
    }

    public function rejectRequest()
    {
        abort(403);
        abort_unless(auth()->user()->can('approve job requests'), 403);

        $pendingApproval = $this->workOrder->pendingApproval(ApprovalRequest::TYPE_DEPARTMENT_MOVE);

        if (! $pendingApproval || ! $pendingApproval->isPending()) {
            $this->dispatch('swal:info', title: 'error', text: 'No pending approval request found or request already processed.');

            return false;
        }

        $result = $pendingApproval->reject(auth()->user(), 'Rejected from job view page');

        if ($result) {
            $this->dispatch('swal:info', title: 'success', text: 'Request rejected.');

            return redirect(request()->header('Referer'));
        } else {
            $this->dispatch('swal:info', title: 'error', text: 'Failed to reject request.');

            return false;
        }
    }

    #[On('customerSelected')]
    public function onCustomerSelected($customerId)
    {
        $this->new_customer = $customerId;
    }

    public function changeCustomer()
    {
        abort(403);
        if ($this->workOrder->bill_id) {
            $this->dispatch('swal:info', title: 'error', text: 'Billed Job cannot change customer');

            return false;
        }

        if ($this->new_customer) {
            $this->workOrder->customer_id = $this->new_customer;
            $this->workOrder->save();

            if ($this->workOrder->customerOrder) {
                OrderHistoryService::logOrderUpdated($this->workOrder->customerOrder, ['customer' => 'changed']);
            }

            $this->dispatch('swal:info', title: 'success', text: 'Customer Changed');

            $this->dispatch('closeCustomerChange');

            return redirect(request()->header('Referer'));
        } else {
            $this->dispatch('swal:info', title: 'error', text: 'Select new customer');
        }
    }

    public function render()
    {
        $viewData = [
            'warehouses' => WareHouse::all(),
            'departments' => Department::all(),
            'bills' => $this->workOrder->customer->bills,
            'customers' => Customer::all(),
        ];

        if ($this->mobile) {
            return view('livewire.work-data-mobile', $viewData);
        }

        return view('livewire.work-data-component', $viewData);
    }
}
