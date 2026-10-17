{{-- Print field rendering partial - static values for PDF export --}}
@php
    $fieldValue = $payload[$item->name] ?? '';
@endphp

<div class="col-{{ $item->columns }}" style="padding: 4px 0 4px 10px;">
    @if($item->type == "Options")
        <table class="input-table">
            <tr>
                <td class="input-title">{{ $item->name }}:</td>
                @foreach(json_decode($item->value) ?? [] as $key => $value)
                    <td>
                        <div class="input-options">
                            <label>{{ $value }}</label>
                            @if(isset($payload[$item->name][$value]) && $payload[$item->name][$value])
                                <span class="checkbox-box checkbox-checked"></span>
                            @else
                                <span class="checkbox-box checkbox-unchecked"></span>
                            @endif
                        </div>
                    </td>
                @endforeach
            </tr>
        </table>
    @elseif($item->type == "Box")
        <table class="input-table">
            <tr>
                <td>
                    <div class="input-options">
                        <label>{{ $item->name }}</label>
                        @if($fieldValue)
                            <span class="checkbox-box checkbox-checked"></span>
                        @else
                            <span class="checkbox-box checkbox-unchecked"></span>
                        @endif
                    </div>
                </td>
            </tr>
        </table>
    @elseif($item->type == "Description")
        <table class="input-table">
            <tr>
                <td class="input-title input-title-sub">{{ $item->name }}</td>
                <td class="value" style="text-align: left !important;width: 100%">
                    <input type="text" value="{{ $fieldValue }}" class="input-box" readonly style="width: 100%;">
                </td>
            </tr>
        </table>
    @else
        <table class="input-table">
            <tr>
                <td class="input-title input-title-sub">{{ $item->name }}</td>
                <td class="value" style="text-align: left !important;width: 100%">
                    <input type="text" value="{{ $fieldValue }}" class="input-box" readonly>
                </td>
            </tr>
        </table>
    @endif
</div>
