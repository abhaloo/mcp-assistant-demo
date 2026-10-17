@props([
    'label',
    'icon',
    'value',
    'cur' => null,
    'series' => [],
    'spark' => 'var(--mcp-navy-700)',
    'sparkLabel' => null,
    'sparkDesc' => null,
    'deltaKind' => 'flat',
    'deltaText' => null,
    'hint' => null,
    'href' => null,
    'interactive' => false,
    'navLabel' => null,
])

@php
    $n = count($series);
    $values = array_map(fn ($p) => (int) ($p['value'] ?? 0), $series);
    $max = ($values !== [] ? max($values) : 0) ?: 1;

    $plot = [];
    $hitWidth = $n > 1 ? 100 / ($n - 1) : 100;
    foreach ($series as $i => $p) {
        $raw = (int) ($p['value'] ?? 0);
        $plot[] = [
            'x' => $n > 1 ? ($i / ($n - 1)) * 100 : 50,
            'y' => 32 - ($raw / $max * 26) - 2,
            'label' => $p['label'] ?? '',
            'amount' => $p['amount'] ?? '',
            'isLast' => $i === $n - 1,
        ];
    }

    $pts = $plot !== [] ? implode(' ', array_map(fn ($p) => round($p['x'], 2) . ',' . round($p['y'], 2), $plot)) : '';
    $deltaSr = match ($deltaKind) {
        'up' => 'increased',
        'down' => 'decreased',
        default => 'unchanged',
    };
    $deltaArrow = match ($deltaKind) {
        'up' => '↑',
        'down' => '↓',
        default => '→',
    };
    $linkLabel = $navLabel ?? trim($label . ', ' . ($cur ? $cur . ' ' : '') . $value);
@endphp

<div {{ $attributes->class([
    'mcp-kpi',
    'mcp-kpi--link' => (bool) $href,
    'mcp-kpi--interactive' => $interactive && $plot !== [],
]) }}>
    @if($href)
        <a href="{{ $href }}" class="mcp-kpi__stretch"
            aria-label="{{ $linkLabel }}"></a>
    @endif

    <div class="mcp-kpi__top">
        <span class="mcp-kpi__label">{{ $label }}</span>
        <span class="mcp-kpi__ic"><i class="fa {{ $icon }}"></i></span>
    </div>
    <div class="mcp-kpi__value">@if($cur)<span class="cur">{{ $cur }}</span>@endif{{ $value }}</div>

    @if($n > 1 && $pts)
        <div class="mcp-kpi__spark-wrap">
            <svg class="mcp-kpi__spark" viewBox="0 0 100 32" preserveAspectRatio="none"
                role="img" aria-labelledby="{{ $uid = 'mcp-kpi-spark-' . \Illuminate\Support\Str::slug($label) }}-title {{ $uid }}-desc">
                <title id="{{ $uid }}-title">{{ $sparkLabel ?? ($label . ' trend') }}</title>
                <desc id="{{ $uid }}-desc">{{ $sparkDesc ?? 'Daily values across the selected period.' }}</desc>
                <polyline fill="none" stroke="{{ $spark }}" stroke-width="1.5" points="{{ $pts }}"/>
                @foreach($plot as $p)
                    @if($interactive)
                        <rect class="mcp-kpi__hit" x="{{ round($p['x'] - $hitWidth / 2, 2) }}" y="0"
                            width="{{ round($hitWidth, 2) }}" height="32"
                            data-label="{{ $p['label'] }}"
                            data-amount="{{ $p['amount'] }}"
                            data-x="{{ round($p['x'], 2) }}"
                            data-y="{{ round($p['y'], 2) }}" />
                    @endif
                    <circle class="mcp-kpi__dot{{ $p['isLast'] ? ' mcp-kpi__dot--last' : '' }}"
                        cx="{{ round($p['x'], 2) }}" cy="{{ round($p['y'], 2) }}"
                        r="{{ $p['isLast'] ? '2' : '1.6' }}"
                        @unless($p['isLast']) opacity="0" @endunless />
                @endforeach
            </svg>
            @if($interactive)
                <div class="mcp-kpi__tip" role="tooltip" aria-hidden="true"></div>
            @endif
        </div>
    @endif

    <div class="mcp-kpi__foot">
        @if($deltaText)
            <span class="mcp-delta mcp-delta--{{ $deltaKind }}">
                <span class="mcp-delta__arrow" aria-hidden="true">{{ $deltaArrow }}</span>
                <span class="visually-hidden">{{ $deltaSr }}</span>
                {{ $deltaText }}
            </span>
        @endif
        @if($hint)<span class="mcp-kpi__hint">{{ $hint }}</span>@endif
    </div>
</div>

@if($interactive && $n > 1 && $pts)
    @once
        @push('scripts')
            <script>
            (function () {
                function bindKpiSparkTips(root) {
                    root.querySelectorAll('.mcp-kpi--interactive .mcp-kpi__spark-wrap').forEach(function (wrap) {
                        if (wrap.dataset.kpiBound) return;
                        wrap.dataset.kpiBound = '1';

                        var tip = wrap.querySelector('.mcp-kpi__tip');
                        var svg = wrap.querySelector('.mcp-kpi__spark');
                        if (!tip || !svg) return;

                        function showTip(hit) {
                            var label = hit.getAttribute('data-label') || '';
                            var amount = hit.getAttribute('data-amount') || '';
                            tip.textContent = label && amount ? (label + ' · ' + amount) : (label || amount);
                            var xPct = parseFloat(hit.getAttribute('data-x') || '0');
                            var yPct = parseFloat(hit.getAttribute('data-y') || '0');
                            tip.style.left = xPct + '%';
                            tip.style.top = (yPct / 32 * 100) + '%';
                            tip.classList.add('is-visible');
                            wrap.querySelectorAll('.mcp-kpi__dot').forEach(function (dot) {
                                dot.setAttribute('opacity', '0');
                            });
                            var idx = Array.prototype.indexOf.call(wrap.querySelectorAll('.mcp-kpi__hit'), hit);
                            var dots = wrap.querySelectorAll('.mcp-kpi__dot');
                            if (dots[idx]) {
                                dots[idx].setAttribute('opacity', '1');
                            }
                        }

                        function hideTip() {
                            tip.classList.remove('is-visible');
                            wrap.querySelectorAll('.mcp-kpi__dot').forEach(function (dot) {
                                dot.setAttribute('opacity', dot.classList.contains('mcp-kpi__dot--last') ? '1' : '0');
                            });
                        }

                        wrap.querySelectorAll('.mcp-kpi__hit').forEach(function (hit) {
                            hit.addEventListener('mouseenter', function () { showTip(hit); });
                            hit.addEventListener('mouseleave', hideTip);
                            hit.addEventListener('touchstart', function () { showTip(hit); }, { passive: true });
                            hit.addEventListener('click', function (e) {
                                e.preventDefault();
                                e.stopPropagation();
                            });
                        });
                    });
                }

                if (document.readyState === 'loading') {
                    document.addEventListener('DOMContentLoaded', function () { bindKpiSparkTips(document); });
                } else {
                    bindKpiSparkTips(document);
                }
                document.addEventListener('livewire:init', function () { bindKpiSparkTips(document); });
            })();
            </script>
        @endpush
    @endonce
@endif
