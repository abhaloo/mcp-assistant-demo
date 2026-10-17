<div>
    <div class="mcp mcp-page--orders">
        <x-mcp.page-header
            :title="'Order #'.$customerOrder->order_number"
            :subtitle="$orderStatus['message']">
            <x-slot:breadcrumb>
                <x-mcp.breadcrumb :items="[
                    ['label' => 'Customer Orders', 'href' => route('customer-orders.index')],
                    ['label' => '#'.$customerOrder->order_number],
                ]" />
            </x-slot:breadcrumb>
            <x-slot:titleAdornment>
                <x-mcp.status :kind="$orderStatus['color']" :label="$orderStatus['status']" />
            </x-slot:titleAdornment>
            <x-slot:actions>
                @if($orderStatus['status'] !== 'CANCELLED' && $customerOrder->status !== 'FINISHED')
                    @can('cancel customer order')
                        <x-mcp.btn variant="success" size="sm" icon="fa-check"
                            data-coreui-toggle="modal" data-coreui-target="#modal-finish-order">Finish Order</x-mcp.btn>
                        <x-mcp.btn variant="danger" size="sm" icon="fa-ban"
                            data-coreui-toggle="modal" data-coreui-target="#modal-cancel-order">Cancel</x-mcp.btn>
                    @endcan
                @endif
                @if($orderStatus['status'] === 'CANCELLED' || $customerOrder->status === 'FINISHED')
                    @can('reopen customer order')
                        <x-mcp.btn variant="warning" size="sm" icon="fa-redo"
                            data-coreui-toggle="modal" data-coreui-target="#modal-reopen-order">Re-open</x-mcp.btn>
                    @endcan
                @endif
                <x-mcp.btn variant="secondary" size="sm" icon="fa-arrow-left"
                    :href="route('customer-orders.index')">Back</x-mcp.btn>
            </x-slot:actions>
        </x-mcp.page-header>

        <div class="row g-3">
            <div class="col-md-4">
                <x-mcp.card title="Order Details" class="mb-3">
                    <x-mcp.deflist>
                        <x-mcp.deflist.row label="Order #">
                            #{{ $customerOrder->order_number }}
                        </x-mcp.deflist.row>

                        <x-mcp.deflist.row label="Customer">
                            <a href="{{ route('customer.show', $customerOrder->customer) }}" class="mcp-link">
                                {{ $customerOrder->customer->name }}
                            </a>
                            <x-slot:action>
                                @can('Edit customer order details')
                                    @if($orderStatus['status'] !== 'CANCELLED' && !$customerOrder->hasInvoice())
                                        <x-mcp.icon-btn icon="fa-pen" title="Change customer"
                                            wire:click="openChangeCustomerModal"
                                            data-coreui-toggle="modal" data-coreui-target="#modal-change-customer" />
                                    @endif
                                @endcan
                            </x-slot:action>
                        </x-mcp.deflist.row>

                        <x-mcp.deflist.row label="Created">
                            {{ $customerOrder->created_at->format('d M, Y · H:i') }}
                        </x-mcp.deflist.row>

                        <x-mcp.deflist.row label="Description">
                            {{ $customerOrder->description ?: '-' }}
                            <x-slot:action>
                                @can('Edit customer order details')
                                    @if($orderStatus['status'] !== 'CANCELLED' && !$customerOrder->hasInvoice())
                                        <x-mcp.icon-btn icon="fa-pen" title="Edit description"
                                            wire:click="openChangeDescriptionModal"
                                            data-coreui-toggle="modal" data-coreui-target="#modal-change-description" />
                                    @endif
                                @endcan
                            </x-slot:action>
                        </x-mcp.deflist.row>
                    </x-mcp.deflist>
                </x-mcp.card>

                <x-mcp.card title="Bills & Invoices" :count="$customerOrder->bills->count()">
                    @forelse($customerOrder->bills as $bill)
                        <div class="d-flex justify-content-between align-items-center mb-2 pb-2 border-bottom">
                            <x-mcp.bill-chip :bill="$bill" />
                            <div class="mcp-row-actions">
                                <x-mcp.btn variant="primary" size="sm" icon="fa-eye"
                                    :href="route('bill.show', ['bill' => $bill, 'from_order' => $customerOrder->id])">
                                    View
                                </x-mcp.btn>
                                @if($bill->type == 'Quotation')
                                    <x-mcp.btn variant="secondary" size="sm" icon="fa-edit"
                                        :href="route('bill.create', ['bill' => $bill, 'from_order' => $customerOrder->id])">
                                        Edit
                                    </x-mcp.btn>
                                    @can('edit customer order')
                                        <x-mcp.btn variant="danger" size="sm" icon="fa-unlink"
                                            wire:click="detachBill({{ $bill->id }})"
                                            onclick="return confirm('Are you sure you want to detach this quotation from the order?')">
                                            Detach
                                        </x-mcp.btn>
                                    @endcan
                                @endif
                            </div>
                        </div>
                        @if($bill->type == 'Quotation' && $orderStatus['status'] !== 'CANCELLED')
                            <div class="text-center mt-2 mb-3">
                                <small class="text-muted d-block mb-2">Ready to bill the customer?</small>
                                <div class="d-flex justify-content-center gap-2">
                                    <x-mcp.btn variant="success" size="sm" icon="fa-check"
                                        data-coreui-toggle="modal" data-coreui-target="#modal-convert-invoice-{{ $bill->id }}">
                                        Convert to Invoice
                                    </x-mcp.btn>
                                    @if(auth()->user()->role == 'admin')
                                        <x-mcp.btn variant="warning" size="sm" icon="fa-check"
                                            data-coreui-toggle="modal" data-coreui-target="#modal-convert-payable-{{ $bill->id }}">
                                            Convert to Payable
                                        </x-mcp.btn>
                                    @endif
                                </div>
                            </div>
                        @endif
                    @empty
                        <x-mcp.empty title="No quotation or invoice yet" hint="Use the buttons below to create one." />
                    @endforelse

                    @if($orderStatus['status'] !== 'CANCELLED')
                        <hr class="mcp-divider">
                        <div class="d-flex flex-wrap justify-content-center gap-2">
                            @can('add quotation')
                                <x-mcp.btn variant="primary" size="sm" icon="fa-plus"
                                    :href="route('bill.create', ['type' => 'Quotation', 'customer_order' => $customerOrder->id, 'customer_id' => $customerOrder->customer_id, 'from_order' => $customerOrder->id])">
                                    Quotation
                                </x-mcp.btn>
                            @endcan
                            @can('add invoice')
                                <x-mcp.btn variant="success" size="sm" icon="fa-plus"
                                    :href="route('bill.create', ['type' => 'Invoice', 'customer_order' => $customerOrder->id, 'customer_id' => $customerOrder->customer_id, 'from_order' => $customerOrder->id])">
                                    Invoice
                                </x-mcp.btn>
                            @endcan
                            @can('edit customer order')
                                <x-mcp.btn variant="ghost" size="sm" icon="fa-link"
                                    data-coreui-toggle="modal" data-coreui-target="#modal-attach-bill">
                                    Attach Existing
                                </x-mcp.btn>
                            @endcan
                            @if(auth()->user()->role == 'admin')
                                <x-mcp.btn variant="warning" size="sm" icon="fa-plus"
                                    :href="route('bill.create', ['type' => 'Payable Quotation', 'customer_order' => $customerOrder->id, 'customer_id' => $customerOrder->customer_id, 'from_order' => $customerOrder->id])">
                                    Payable Quotation
                                </x-mcp.btn>
                            @endif
                        </div>
                    @endif
                </x-mcp.card>
            </div>

            <div class="col-md-8">
                <x-mcp.card title="Jobs" :count="$customerOrder->workOrders->count()" class="mb-3" flush>
                    <x-slot:actions>
                        @if($orderStatus['status'] !== 'CANCELLED' && $customerOrder->status !== 'FINISHED')
                            @can('add job')
                                <x-mcp.btn variant="ghost" size="sm" icon="fa-link"
                                    data-coreui-toggle="modal" data-coreui-target="#modal-attach-job">Attach</x-mcp.btn>
                                <x-mcp.btn variant="primary" size="sm" icon="fa-plus"
                                    data-coreui-toggle="modal" data-coreui-target="#modal-new-job">New Job</x-mcp.btn>
                            @endcan
                        @endif
                    </x-slot:actions>

                    <div class="mcp-table-wrap"
                        wire:loading.class="mcp-skeleton-refreshing"
                        wire:loading.attr="aria-busy"
                        data-mcp-page-context-owner="jobs"
                        data-mcp-context-route="customer-orders.show"
                        data-mcp-context-kind="embedded_list"
                        data-mcp-context-resource="work_order"
                        data-mcp-context-id="jobs:customer-order:{{ $customerOrder->id }}"
                        data-mcp-context-scope-id="{{ $customerOrder->id }}"
                    >
                        <table class="mcp-table">
                            <thead>
                                <tr>
                                    <th>Job</th>
                                    <th>Bill</th>
                                    <th>Title</th>
                                    <th>Status</th>
                                    <th>Department</th>
                                    <th class="mcp-td--right">Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                @forelse($customerOrder->workOrders as $work_order)
                                    @php($isPriority = $work_order->priority === 'high' && $work_order->department_id != 12)
                                    <tr wire:key="show-wo-{{ $work_order->id }}" @class(['mcp-row--priority' => $isPriority]) data-mcp-context-job-id="{{ $work_order->id }}">
                                        <td>
                                            <div class="mcp-stack">
                                                <a href="{{ route('work-order.show', ['work_order' => $work_order, 'from_order' => $customerOrder->id]) }}" class="mcp-id-link">
                                                    #{{ $work_order->work_number ?? $work_order->id }}
                                                </a>
                                                @if($isPriority)
                                                    <x-mcp.status kind="danger" size="sm" label="HIGH" />
                                                @endif
                                            </div>
                                        </td>
                                        <td>
                                            @if($work_order->bill)
                                                <x-mcp.bill-chip :bill="$work_order->bill" :href="auth()->user()?->canany(['view invoice', 'view quotation']) ? route('bill.show', $work_order->bill) : null" />
                                            @else
                                                <span class="mcp-em-dash">—</span>
                                            @endif
                                        </td>
                                        <td class="mcp-td--mute"><div class="mcp-truncate">{{ $work_order->title }}</div></td>
                                        <td>
                                            <x-mcp.status :kind="$work_order->statusKind()" size="sm" :label="$work_order->statusLabel()" />
                                        </td>
                                        <td><x-mcp.dept-badge :department="$work_order->department" /></td>
                                        <td class="mcp-td--right">
                                            <div class="mcp-row-actions">
                                                <x-mcp.icon-btn variant="primary" icon="fa-eye" title="View"
                                                    :href="route('work-order.show', ['work_order' => $work_order, 'from_order' => $customerOrder->id])" />
                                                @if(!$customerOrder->hasInvoice())
                                                    <x-mcp.icon-btn variant="danger" icon="fa-unlink" title="Detach"
                                                        wire:click="openDetachModal({{ $work_order->id }})"
                                                        data-coreui-toggle="modal" data-coreui-target="#modal-detach-job" />
                                                @endif
                                            </div>
                                        </td>
                                    </tr>
                                @empty
                                    <tr><td colspan="6">
                                        <x-mcp.empty title="No jobs yet"
                                            hint="Attach or create a job to start production on this order." />
                                    </td></tr>
                                @endforelse
                            </tbody>
                        </table>
                    </div>
                </x-mcp.card>

                @if($customerOrder->histories->count() > 0)
                    <x-mcp.card title="Activity" :count="$customerOrder->histories->count()" flush>
                        @foreach($customerOrder->histories->take(10) as $entry)
                            <div class="mcp-activity-item">
                                <div class="mcp-activity-item__icon">
                                    <i class="fa fa-history"></i>
                                </div>
                                <div class="mcp-activity-item__body">
                                    <div class="mcp-activity-item__desc">{{ $entry->description ?? $entry->action }}</div>
                                    <div class="mcp-activity-item__meta">
                                        <i class="fa fa-user mcp-icon--xxs"></i>
                                        {{ $entry->user->name ?? 'System' }} ·
                                        {{ $entry->created_at->format('d M y, H:i') }}
                                    </div>
                                </div>
                            </div>
                        @endforeach
                    </x-mcp.card>
                @endif
            </div>
        </div>
    </div>

    <div class="modal fade" wire:ignore.self id="modal-new-job" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header py-2">
                    <h5 class="modal-title">Create New Job</h5>
                    <button type="button" class="btn-close" data-coreui-dismiss="modal"></button>
                </div>
                <div class="modal-body">
                    <div class="mb-3">
                        <label class="form-label">Job Title <span class="text-danger">*</span></label>
                        <input type="text" wire:model.live="title"
                            class="form-control form-control-sm @error('title') is-invalid @enderror"
                            placeholder="Short title to identify this job">
                        @error('title')
                            <div class="invalid-feedback">{{ $message }}</div>
                        @enderror
                    </div>

                    <div class="mb-3">
                        <label class="form-label">Specification Template <span class="text-danger">*</span></label>
                        <select wire:model.live="specification_id"
                            class="form-select form-select-sm @error('specification_id') is-invalid @enderror">
                            <option value="">-- Select --</option>
                            @foreach($specifications as $spec)
                                <option value="{{ $spec->id }}">{{ $spec->name }}</option>
                            @endforeach
                        </select>
                        @error('specification_id')
                            <div class="invalid-feedback">{{ $message }}</div>
                        @enderror
                    </div>

                    <div class="alert alert-info py-2 small mb-0">
                        <i class="fa fa-info-circle"></i> Order {{ $customerOrder->order_number }} -
                        {{ $customerOrder->customer->name }}
                    </div>
                </div>
                <div class="modal-footer py-2">
                    <button type="button" wire:click="createJob" class="btn btn-primary btn-sm"
                        wire:loading.attr="disabled">
                        <span wire:loading.remove wire:target="createJob">
                            <i class="fa fa-plus"></i> Create Job
                        </span>
                        <span wire:loading wire:target="createJob">
                            <i class="fa fa-spinner fa-spin"></i> Creating...
                        </span>
                    </button>
                    <button type="button" class="btn btn-secondary btn-sm" data-coreui-dismiss="modal">Cancel</button>
                </div>
            </div>
        </div>
    </div>

    <div class="modal fade" wire:ignore.self id="modal-attach-job" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header py-2">
                    <h5 class="modal-title">Attach Existing Job</h5>
                    <button type="button" class="btn-close" data-coreui-dismiss="modal"></button>
                </div>
                <div class="modal-body">
                    <div class="mb-3">
                        <label class="form-label">Select Unlinked Job</label>
                        <select wire:model.live="attach_work_order_id"
                            class="form-select form-select-sm @error('attach_work_order_id') is-invalid @enderror">
                            <option value="">-- Select Job --</option>
                            @foreach($unlinkedJobs as $job)
                                <option value="{{ $job->id }}">
                                    #{{ $job->work_number ?? $job->id }} - {{ $job->title }}
                                </option>
                            @endforeach
                        </select>
                        @error('attach_work_order_id')
                            <div class="invalid-feedback">{{ $message }}</div>
                        @enderror
                    </div>
                    @if($unlinkedJobs->count() > 0)
                        <small class="text-muted">
                            <i class="fa fa-info-circle"></i> Showing {{ $unlinkedJobs->count() }} unlinked job(s) for
                            {{ $customerOrder->customer->name }}.
                        </small>
                    @else
                        <div class="alert alert-info py-2 small mb-0">
                            <i class="fa fa-check-circle"></i> No unlinked jobs for this customer.
                        </div>
                    @endif
                </div>
                <div class="modal-footer py-2">
                    <button type="button" wire:click="attachJob" class="btn btn-primary btn-sm">
                        Attach Job
                    </button>
                    <button type="button" class="btn btn-secondary btn-sm" data-coreui-dismiss="modal">Cancel</button>
                </div>
            </div>
        </div>
    </div>

    <div class="modal fade" wire:ignore.self id="modal-attach-bill" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header py-2">
                    <h5 class="modal-title">Attach Existing Bill</h5>
                    <button type="button" class="btn-close" data-coreui-dismiss="modal"></button>
                </div>
                <div class="modal-body">
                    <div class="mb-3">
                        <label class="form-label">Select Unlinked Bill</label>
                        <select wire:model.live="attach_bill_id"
                            class="form-select form-select-sm @error('attach_bill_id') is-invalid @enderror">
                            <option value="">-- Select Bill --</option>
                            @foreach($unlinkedBills as $bill)
                                <option value="{{ $bill->id }}">
                                    {{ $bill->type }} #{{ $bill->invoice_number ?? $bill->id }} -
                                    {{ $bill->created_at->format('d M, Y') }}
                                </option>
                            @endforeach
                        </select>
                        @error('attach_bill_id')
                            <div class="invalid-feedback">{{ $message }}</div>
                        @enderror
                    </div>
                    @if($unlinkedBills->count() > 0)
                        <small class="text-muted">
                            <i class="fa fa-info-circle"></i> Showing {{ $unlinkedBills->count() }} unlinked bill(s) for
                            {{ $customerOrder->customer->name }}.
                        </small>
                    @else
                        <div class="alert alert-info py-2 small mb-0">
                            <i class="fa fa-check-circle"></i> No unlinked bills for this customer.
                        </div>
                    @endif
                </div>
                <div class="modal-footer py-2">
                    <button type="button" wire:click="attachBill" class="btn btn-primary btn-sm">
                        Attach Bill
                    </button>
                    <button type="button" class="btn btn-secondary btn-sm" data-coreui-dismiss="modal">Cancel</button>
                </div>
            </div>
        </div>
    </div>


    @can('Edit customer order details')
        {{-- Change Customer Modal --}}
        <div class="modal fade" wire:ignore.self id="modal-change-customer" tabindex="-1" aria-hidden="true">
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content">
                    <div class="modal-header py-2">
                        <h5 class="modal-title">Change Customer</h5>
                        <button type="button" class="btn-close" data-coreui-dismiss="modal"></button>
                    </div>
                    <div class="modal-body">
                        <div class="mb-3">
                            <div class="form-check form-switch mb-2">
                                <input type="checkbox" wire:model.live="change_customer_is_new" class="form-check-input"
                                    id="changeNewCustomerToggle">
                                <label class="form-check-label" for="changeNewCustomerToggle">
                                    Create new customer
                                </label>
                            </div>

                            @if($change_customer_is_new)
                                <label class="form-label">New Customer Name <span class="text-danger">*</span></label>
                                <input type="text" wire:model.live="change_new_customer_name"
                                    class="form-control form-control-sm @error('change_new_customer_name') is-invalid @enderror"
                                    placeholder="Enter new customer name">
                                @error('change_new_customer_name')
                                    <div class="invalid-feedback">{{ $message }}</div>
                                @enderror
                            @else
                                @livewire('customer-search-component', key('change-customer-search'))
                            @endif
                        </div>

                        <div class="alert alert-info py-2 small mb-0">
                            <i class="fa fa-info-circle"></i>
                            Changing the customer will update linked jobs and invoices.
                        </div>
                    </div>
                    <div class="modal-footer py-2">
                        <button type="button" wire:click="updateCustomer" class="btn btn-primary btn-sm"
                            wire:loading.attr="disabled">
                            <span wire:loading.remove wire:target="updateCustomer">Save</span>
                            <span wire:loading wire:target="updateCustomer">
                                <i class="fa fa-spinner fa-spin"></i> Saving...
                            </span>
                        </button>
                        <button type="button" class="btn btn-secondary btn-sm" data-coreui-dismiss="modal">Cancel</button>
                    </div>
                </div>
            </div>
        </div>

        {{-- Change Description Modal --}}
        <div class="modal fade" wire:ignore.self id="modal-change-description" tabindex="-1" aria-hidden="true">
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content">
                    <div class="modal-header py-2">
                        <h5 class="modal-title">Change Description</h5>
                        <button type="button" class="btn-close" data-coreui-dismiss="modal"></button>
                    </div>
                    <div class="modal-body">
                        <div class="mb-3">
                            <label class="form-label">Description</label>
                            <textarea wire:model.live="change_description"
                                class="form-control form-control-sm @error('change_description') is-invalid @enderror"
                                rows="3" placeholder="Order description (optional)"></textarea>
                            @error('change_description')
                                <div class="invalid-feedback">{{ $message }}</div>
                            @enderror
                        </div>
                    </div>
                    <div class="modal-footer py-2">
                        <button type="button" wire:click="updateDescription" class="btn btn-primary btn-sm"
                            wire:loading.attr="disabled">
                            <span wire:loading.remove wire:target="updateDescription">Save</span>
                            <span wire:loading wire:target="updateDescription">
                                <i class="fa fa-spinner fa-spin"></i> Saving...
                            </span>
                        </button>
                        <button type="button" class="btn btn-secondary btn-sm" data-coreui-dismiss="modal">Cancel</button>
                    </div>
                </div>
            </div>
        </div>
    @endcan

    @can('cancel customer order')
        <div class="modal fade" wire:ignore.self id="modal-cancel-order" tabindex="-1" aria-hidden="true">
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content">
                    <div class="modal-header py-2">
                        <h5 class="modal-title">Cancel Order</h5>
                        <button type="button" class="btn-close" data-coreui-dismiss="modal"></button>
                    </div>
                    <div class="modal-body">
                        <p>Are you sure you want to cancel order <strong>#{{ $customerOrder->order_number }}</strong>?</p>

                        <div class="mb-3">
                            <label class="form-label">Reason for Cancellation <span class="text-danger">*</span></label>
                            <textarea wire:model.live="cancel_reason"
                                class="form-control form-control-sm @error('cancel_reason') is-invalid @enderror" rows="3"
                                placeholder="Please provide a reason for cancelling this order..."></textarea>
                            @error('cancel_reason')
                                <div class="invalid-feedback">{{ $message }}</div>
                            @enderror
                        </div>

                        <p class="text-muted small mb-0"><i class="fa fa-exclamation-triangle text-warning"></i> This action
                            cannot be undone.</p>
                    </div>
                    <div class="modal-footer py-2">
                        <button type="button" class="btn btn-secondary btn-sm" data-coreui-dismiss="modal">Close</button>
                        <button type="button" wire:click="cancelOrder" class="btn btn-danger btn-sm">
                            Yes, Cancel Order
                        </button>
                    </div>
                </div>
            </div>
        </div>
    @endcan

    @can('cancel customer order')
        <div class="modal fade" wire:ignore.self id="modal-finish-order" tabindex="-1" aria-hidden="true">
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content">
                    <div class="modal-header py-2">
                        <h5 class="modal-title">Finish Order</h5>
                        <button type="button" class="btn-close" data-coreui-dismiss="modal"></button>
                    </div>
                    <div class="modal-body">
                        <p>Are you sure you want to mark order <strong>#{{ $customerOrder->order_number }}</strong> as
                            finished?</p>
                        <p class="text-muted small mb-0"><i class="fa fa-info-circle text-info"></i> This will mark the
                            order as finished.</p>
                    </div>
                    <div class="modal-footer py-2">
                        <button type="button" class="btn btn-secondary btn-sm" data-coreui-dismiss="modal">Close</button>
                        <button type="button" wire:click="finishOrder" class="btn btn-success btn-sm">
                            Yes, Finish Order
                        </button>
                    </div>
                </div>
            </div>
        </div>
    @endcan

    @can('reopen customer order')
        <div class="modal fade" wire:ignore.self id="modal-reopen-order" tabindex="-1" aria-hidden="true">
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content">
                    <div class="modal-header py-2">
                        <h5 class="modal-title">Re-open Order</h5>
                        <button type="button" class="btn-close" data-coreui-dismiss="modal"></button>
                    </div>
                    <div class="modal-body">
                        <p>Are you sure you want to re-open order <strong>#{{ $customerOrder->order_number }}</strong>?</p>
                        <p class="text-muted small mb-0">
                            <i class="fa fa-info-circle text-info"></i>
                            This will allow jobs and invoices to be attached to this order again.
                        </p>
                    </div>
                    <div class="modal-footer py-2">
                        <button type="button" class="btn btn-secondary btn-sm" data-coreui-dismiss="modal">Close</button>
                        <button type="button" wire:click="reopenOrder" class="btn btn-warning btn-sm">
                            <i class="fa fa-redo"></i> Yes, Re-open Order
                        </button>
                    </div>
                </div>
            </div>
        </div>
    @endcan

    <div class="modal fade" wire:ignore.self id="modal-detach-job" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header py-2">
                    <h5 class="modal-title">Detach Job</h5>
                    <button type="button" class="btn-close" data-coreui-dismiss="modal"></button>
                </div>
                <div class="modal-body">
                    <p>Are you sure you want to detach this job from the order?</p>
                    <p class="text-muted small mb-0">The job will remain but will no longer be linked to this order.</p>
                </div>
                <div class="modal-footer py-2">
                    <button type="button" class="btn btn-secondary btn-sm" data-coreui-dismiss="modal">Cancel</button>
                    <button type="button" wire:click="confirmDetachJob" class="btn btn-danger btn-sm"
                        data-coreui-dismiss="modal">
                        Yes, Detach Job
                    </button>
                </div>
            </div>
        </div>
    </div>

    {{-- Conversion modals for each quotation bill --}}
    @foreach($customerOrder->bills as $bill)
        @if($bill->type == 'Quotation')
            <x-modal.confirm id="modal-convert-invoice-{{ $bill->id }}" title="Convert to Invoice"
                action="{{ route('bill.convert', $bill) }}" confirm-text="Yes, Convert to Invoice" confirm-class="btn-success">
                <p>Are you sure you want to convert this quotation to a <strong>Tax Invoice</strong>?</p>
                <p class="text-muted small mb-0"><i class="fa fa-info-circle text-info"></i> This will generate an invoice
                    number and create financial transactions.</p>
            </x-modal.confirm>

            @if(auth()->user()->role == 'admin')
                <x-modal.confirm id="modal-convert-payable-{{ $bill->id }}" title="Convert to Payable Quotation"
                    action="{{ route('bill.convert.payable', $bill) }}" confirm-text="Yes, Convert to Payable"
                    confirm-class="btn-warning">
                    <p>Are you sure you want to convert this quotation to a <strong>Payable Quotation</strong>?</p>
                </x-modal.confirm>
            @endif
        @endif
    @endforeach

    @script
        <script>
            $wire.on('closeJobModal', () => {
                $('#modal-new-job').modal('hide');
            });
            $wire.on('closeAttachModal', () => {
                $('#modal-attach-job').modal('hide');
            });
            $wire.on('closeChangeCustomerModal', () => {
                $('#modal-change-customer').modal('hide');
            });
            $wire.on('closeChangeDescriptionModal', () => {
                $('#modal-change-description').modal('hide');
            });
            $wire.on('closeAttachBillModal', () => {
                $('#modal-attach-bill').modal('hide');
            });
            $wire.on('closeCancelModal', () => {
                $('#modal-cancel-order').modal('hide');
            });
            $wire.on('closeFinishModal', () => {
                $('#modal-finish-order').modal('hide');
            });
            $wire.on('closeReopenModal', () => {
                $('#modal-reopen-order').modal('hide');
            });
            $wire.on('closeConvertInvoiceModal', () => {
                $('#modal-convert-invoice').modal('hide');
            });
            $wire.on('closeConvertPayableModal', () => {
                $('#modal-convert-payable').modal('hide');
            });

            const historyCollapse = document.getElementById('orderHistoryCollapse');
            const historyIcon = document.getElementById('historyCollapseIcon');
            if (historyCollapse && historyIcon && !historyCollapse.dataset.mcpHistoryBound) {
                historyCollapse.dataset.mcpHistoryBound = '1';
                historyCollapse.addEventListener('show.coreui.collapse', () => {
                    historyIcon.classList.remove('fa-chevron-right');
                    historyIcon.classList.add('fa-chevron-down');
                });
                historyCollapse.addEventListener('hide.coreui.collapse', () => {
                    historyIcon.classList.remove('fa-chevron-down');
                    historyIcon.classList.add('fa-chevron-right');
                });
            }
        </script>
    @endscript
</div>