<?php

declare(strict_types=1);

return [
    'ask_v2_enabled' => (bool) env('RAG_ASK_V2_ENABLED', false),
    // Absolute ingress deadline budget for the v2 stream, in milliseconds.
    // Computed once at server ingress after Form Request validation; a
    // browser-supplied deadline never extends or replaces this value.
    'deadline_budget_ms' => (int) env('ASK_AI_V2_DEADLINE_BUDGET_MS', 600000),
    'budget_multiplier' => (float) env('ASK_AI_BUDGET_MULTIPLIER', 1),

    // Optional v2 surfaces, each mounted on top of the standard panel. Every
    // one can be switched off on its own; with all of them off the panel still
    // answers on the v2 transport. Add a module name here when a new surface
    // is registered in resources/js/ask_ai_v2/index.js.
    'v2_modules' => [
        'activity_spine' => (bool) env('ASK_AI_V2_MODULE_ACTIVITY_SPINE', true),
        'result_table' => (bool) env('ASK_AI_V2_MODULE_RESULT_TABLE', true),
        'interaction_card' => (bool) env('ASK_AI_V2_MODULE_INTERACTION_CARD', true),
        'thought_stream' => (bool) env('ASK_AI_V2_MODULE_THOUGHT_STREAM', true),
    ],

    // Parts of a finished answer that can be switched off on their own, on by
    // default. The panel advertises each switch as a data attribute; the UI
    // reads it at paint time, so a page can flip it live.
    'answer_parts' => [
        'record_refs' => (bool) env('ASK_AI_RECORD_REFS', true),
        'collapsible_sources' => (bool) env('ASK_AI_COLLAPSIBLE_SOURCES', true),
    ],
    'starter_prompts_by_permission' => [
        'view invoice' => [
            'Compare invoiced revenue in Q1 2026 vs Q4 2025',
            'Which customers owe us the most right now?',
        ],
        'view job' => [
            'How many finished jobs have no invoice?',
        ],
        'default' => [
            'What are your business hours?',
            'How do I create a new quotation?',
            'Explain our offset printing process',
        ],
    ],
];
