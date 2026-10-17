<?php

return [

    /*
    |--------------------------------------------------------------------------
    | Third Party Services
    |--------------------------------------------------------------------------
    |
    | This file is for storing the credentials for third party services such
    | as Mailgun, Postmark, AWS and more. This file provides the de facto
    | location for this type of information, allowing packages to have
    | a conventional file to locate the various service credentials.
    |
    */

    'mailgun' => [
        'domain' => env('MAILGUN_DOMAIN'),
        'secret' => env('MAILGUN_SECRET'),
        'endpoint' => env('MAILGUN_ENDPOINT', 'api.mailgun.net'),
        'scheme' => 'https',
    ],

    'postmark' => [
        'token' => env('POSTMARK_TOKEN'),
    ],

    'ses' => [
        'key' => env('AWS_ACCESS_KEY_ID'),
        'secret' => env('AWS_SECRET_ACCESS_KEY'),
        'region' => env('AWS_DEFAULT_REGION', 'us-east-1'),
    ],

    'rag' => [
        'enabled' => env('RAG_SEARCH_ENABLED', env('RAG_ENABLED', false)),
        // Ask AI v2 rollout switch: when true, UI routes and loads v2 assets/stream.
        'ask_v2_enabled' => (bool) env('RAG_ASK_V2_ENABLED', false),
        // Browser-test only: bind CannedSseRagClient so the served app streams a
        // deterministic SSE body for Dusk. Defaults off; never enable in production.
        'fake_stream' => env('RAG_FAKE_STREAM', false),
        'url' => env('RAG_SERVICE_URL', 'http://127.0.0.1:8000'),
        'http_timeout' => (int) env('RAG_HTTP_TIMEOUT', 630),
        'jwt_secret' => env('RAG_JWT_SECRET'),
        'jwt_iss' => 'laravel-billing',
        'jwt_aud' => 'rag-service',
        // Task B3 rollback switch: when true (default on this branch), mintToken()
        // attaches the v2 `record_access` claim (AiAccessPolicy::snapshot()). When
        // false, mintToken() emits exactly the pre-B3 v1 claim set — byte-identical,
        // no `record_access` key at all (not even null).
        'snapshot_v2' => env('RAG_SNAPSHOT_V2', true),
        // Business-query v3 Financial Analytics grants. DEFAULT OFF until the RAG
        // v3 consumer (module-plan Tasks 4–6) is deployed — accidental v3 emission
        // would fail Literal[2] + extra=forbid claim parse for every principal.
        'snapshot_v3' => env('RAG_SNAPSHOT_V3', false),
        // Account ask budget: consumer-first deploy — flip on only after the RAG
        // reader that declares the ask_budget claim is live on the same environment;
        // rollback disables this flag first.
        'ask_budget_claim' => env('RAG_ASK_BUDGET_CLAIM', false),
        // Task B4 rollback switch: when true (default on this branch),
        // AiSearchController attaches trusted record_context (body field +
        // record_context_digest JWT claim) resolved from a browser-supplied
        // Global Search selection. When false, the controller never even parses
        // an incoming `record_context` request key — byte-identical to pre-B4
        // requests, no body field, no claim.
        'record_context' => env('RAG_RECORD_CONTEXT', true),
        'limits' => [
            'admin' => null,
            'default' => 20,
        ],
        'credits' => [
            'monthly_cap' => (float) env('RAG_AI_MONTHLY_CAP', 1),
            'upgrade_url' => env('RAG_AI_UPGRADE_URL'),
            // v2 turns charge the per-user counter from RAG's priced figure
            // (this_turn_usd on the outcome frame). Default off: the counter
            // has never seen v2 traffic, and the cap 402s at the limit.
            'charge_v2' => (bool) env('RAG_AI_CREDITS_CHARGE_V2', false),
            'model_rates' => [
                'default' => 0.000002,
            ],
        ],
    ],

    'sentry' => [
        // Public DSN for the browser SDK (safe to expose in the page). Leave blank
        // to disable front-end error capture. May reuse the backend project DSN
        // (SENTRY_LARAVEL_DSN) or point at a dedicated front-end project.
        'js_dsn' => env('SENTRY_JS_DSN'),
        'environment' => env('SENTRY_ENVIRONMENT', env('APP_ENV', 'production')),
        'release' => env('SENTRY_RELEASE'),
    ],

];
