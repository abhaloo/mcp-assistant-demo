<?php

return [

    'enabled' => env('INTERACTION_AUDIT_ENABLED', false),

    /*
     * Authenticated GET/POST page navigations on these URI prefixes are logged.
     * Livewire sub-requests are excluded — capture those via action.returned only.
     */
    'route_prefixes' => [
        'bills',
        'customer-orders',
        'jobs',
        'inventory',
        'supplier-bill',
        'customers',
        'customer',
        'suppliers',
        'users',
        'accounts',
        'gls',
        'journals',
        'admin',
        'approvals',
        'product',
    ],

    /*
     * Livewire components (short class names) whose action.returned calls are logged.
     * Null method list = log all public actions on that component.
     */
    'livewire_components' => [
        'BillEditorComponent' => null,
        'CustomerOrderShowComponent' => null,
        'WorkOrderComponent' => null,
        'InventoryComponent' => null,
        'InventoryEditorComponent' => null,
        'UserComponent' => null,
        'SupplierBillComponent' => null,
        'ShowBillComponent' => null,
    ],

];
