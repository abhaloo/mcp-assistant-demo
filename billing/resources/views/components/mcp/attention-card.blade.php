@props(['kind'=>'info','icon','count','label','href'=>'#'])

<a href="{{ $href }}" class="mcp-attn-card mcp-attn-card--{{ $kind }}">

  <span class="mcp-attn-card__ic"><i class="fa {{ $icon }}"></i></span>

  <span class="flex-grow-1"><span class="mcp-attn-card__n d-block">{{ $count }}</span>

    <span class="mcp-attn-card__l">{{ $label }}</span></span>

  <i class="fa fa-chevron-right text-muted"></i>

</a>

