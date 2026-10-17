<?php

return [
    'activated' => false,
    'middleware' => ['web', 'auth', 'permission:system audit'],
    'route_path' => 'admin/user-activity',
    'admin_panel_path' => 'admin/dashboard',
    'delete_limit' => 7,

    'model' => [
        'user' => 'App\\Models\\User',
    ],

    'log_events' => [
        'on_create' => false,
        'on_edit' => false,
        'on_delete' => false,
        'on_login' => false,
        'on_lockout' => false,
    ],
];
