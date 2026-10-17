@props([
    'name' => '',
])

@php
    $initials = collect(preg_split('/\s+/', trim($name)))
        ->filter()
        ->take(2)
        ->map(fn($word) => mb_strtoupper(mb_substr($word, 0, 1)))
        ->implode('');
    if ($initials === '') {
        $initials = '?';
    }
@endphp

<span {{ $attributes->merge(['class' => 'mcp-avatar']) }}>{{ $initials }}</span>
