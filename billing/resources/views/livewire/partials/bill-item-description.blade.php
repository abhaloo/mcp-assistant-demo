<div class="d-flex justify-content-between">
    <div class="text-md-center flex-grow-1">
        @if(isset($item['description']))
            @forelse($item['description'] as  $key => $desc)
                <div class="d-flex justify-content-between">
                    <textarea rows="1" class="form-control form-control-sm mb-1 flex-grow-1 js-autogrow" style="resize:none; overflow:hidden;"
                           wire:model.live="bill_items.{{$item['id']}}.description.{{ $key }}"></textarea>
                    <a href="#" wire:click="removeDescription('{{$item['id']}}', {{ $key }})" class="ms-2">
                        <i class="text-danger fa fa-times"></i></a>
                </div>

            @empty
            @endforelse
        @endif
    </div>

    @if(empty($item['description']))
    <x-mcp.btn variant="secondary" size="sm" class="btn-link p-0 border-0"
        wire:click="addDescription('{{ $item['id'] }}')">
        <i class="fa fa-plus"></i> <small><i>Add Text</i></small>
    </x-mcp.btn>
    @endif
</div>
