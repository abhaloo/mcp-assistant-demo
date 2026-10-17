@if($item->type == "Options")
    <div class="card h-100">
        <div class="card-body p-2">
            <label class="form-label fw-bold mb-1">{{ $item->name }}</label>
            <div class="d-flex flex-wrap gap-2">
                @foreach(json_decode($item->value) ?? [] as $value)
                    <div class="form-check form-check-inline">
                        <input class="form-check-input" type="checkbox" id="preview_{{$item->id}}_{{Str::slug($value)}}">
                        <label class="form-check-label" for="preview_{{$item->id}}_{{Str::slug($value)}}">{{ $value }}</label>
                    </div>
                @endforeach
            </div>
        </div>
    </div>
@elseif($item->type == "Box")
    <div class="card h-100">
        <div class="card-body p-2">
            <div class="form-check">
                <input class="form-check-input" type="checkbox" id="preview_{{$item->id}}">
                <label class="form-check-label fw-bold" for="preview_{{$item->id}}">{{ $item->name }}</label>
            </div>
        </div>
    </div>
@elseif($item->type == "Description")
    <div class="card h-100">
        <div class="card-body p-2">
            <label class="form-label fw-bold mb-1">{{ $item->name }}</label>
            <textarea class="form-control form-control-sm" rows="2" placeholder="Text area..."></textarea>
        </div>
    </div>
@elseif($item->type == "Soft List" || $item->type == "Hard List")
    <div class="card h-100">
        <div class="card-body p-2">
            <label class="form-label fw-bold mb-1">{{ $item->name }}</label>
            <input type="text" class="form-control form-control-sm" list="preview_list_{{$item->id}}" placeholder="Type or select...">
            @if($item->type == "Hard List")
                <datalist id="preview_list_{{$item->id}}">
                    <option>NCR blue-CFB 61x86</option>
                    <option>NCR green-CFB 61x86</option>
                    <option>NCR pink-CFB 61x86</option>
                    <option>NCR white-CB 61x86</option>
                    <option>NCR yellow-CF-55GSM 61x86</option>
                </datalist>
            @endif
        </div>
    </div>
@else
    <div class="card h-100">
        <div class="card-body p-2">
            <label class="form-label fw-bold mb-1">{{ $item->name }}</label>
            <input type="text" class="form-control form-control-sm" placeholder="Text input...">
        </div>
    </div>
@endif
@if($item->department)
    <small class="text-muted"><i class="fa fa-lock me-1"></i>{{ $item->department->name }}</small>
@endif
