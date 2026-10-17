<div class="position-relative">
    <label for="account">{{ is_array($type) ? 'Accounts' : $type }}</label>
    <div class="input-group">
        <input
            type="text"
            id="account"
            class="form-control"
            placeholder="Search {{ is_array($type) ? 'Accounts' : $type }}..."
            wire:model.live="query"
            wire:keydown.escape="resetFields"
            wire:keydown.tab="resetFields"
            wire:keydown.arrow-up="decrementHighlight"
            wire:keydown.arrow-down="incrementHighlight"
            wire:keydown.enter="selectAccount"
        />
        <div class="input-group-append">
            <span class="btn btn-secondary rounded-0" type="button">
                <i class="fa fa-search"></i>
            </span>
        </div>
    </div>

    <div wire:loading class="position-absolute z-999 w-100 rounded-1 bg-info">
        <div class="list-item">Searching...</div>
    </div>

    @if(!empty($query))
        <div class="position-absolute z-999 w-100 bg-white list-group" style="border: #F2F4F6 solid 2px">
            @if(!empty($accounts))
                @foreach($accounts as $i => $account)
                    <a href="#" wire:click="selectAccount({{ $account['id']}})"
                        class="list-item text-dark {{ $highlightIndex === $i ? 'rounded-1 bg-warning' : '' }} bg-info p-1"
                    >{{ $account['name'] }}</a>
                @endforeach
            @else
                <div class="list-item p-1 bg-danger rounded-1 text-light">No results!</div>
            @endif
        </div>
    @endif
</div>
