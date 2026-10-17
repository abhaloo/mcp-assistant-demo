<div>
    <div class="row">
        <div class="col">
            <h1 class="h3 mb-3">Work <strong>Order</strong> for bill# {{ $bill->id }} of <strong>
                    {{ $bill->customer->name }}
                </strong></h1>
        </div>
    </div>

    <div class="card card-body border-0 shadow table-wrapper table-responsive">

        <div class="row">
            <div class="col">
                <div class="row d-flex justify-content-between">
                    <div class="col-4"> <p class="text-success fw-bold">Materials and Details</p></div>
                    <div class="col-6 float-end mb-2">
                        @livewire('product-search-component', ['Material'])
                    </div>
                </div>
                <table class="table table-bordered table-sm bg-gray-100">
                    <tr>
                        <th>Item</th>
                        <th>Qty</th>
                        <th>Unit</th>
                    </tr>

                    <tbody>
                    @forelse($work_items as $item)
                        <tr>
                            <td>{{$item['name']}}</td>
                            <td>
                                <input type="number" step="0.01" class="form-control form-control-sm boxed"
                                       wire:model.live="work_items.{{$item['id']}}.quantity">
                            </td>

                            <td>

                                <input type="text" disabled class="form-control form-control-sm"
                                       wire:model.live="work_items.{{$item['id']}}.unit">
                            </td>

                            <td>
                                <a href="#" class="btn btn-outline-danger text-dark btn-sm"
                                   wire:click="itemDelete({{$item['id']}})"
                                ><i class="fa fa-trash"></i> </a>
                            </td>

                        </tr>
                    @empty
                    @endforelse
                    </tbody>
                </table>

                <textarea wire:model.live="description" class="form-control mb-4" placeholder="Job Description"></textarea>

            </div>
        </div>

        <div class="row">
            <div class="col">
                <p class="text-success fw-bold">Specifications</p>
                <select class="form-control" wire:model.live="specification">
                    <option>Choose</option>
                @foreach($specifications as $specification)
                    <option value="{{ $specification->id }}">{{ $specification->name }}</option>
                @endforeach
                </select>

                <table class="table table-bordered table-sm mt-3">
                    @if($items)
                        @foreach($items as $item)
                            <tr>
                                <th>{{ $item->name }}</th>
                                @switch($item->type)
                                    @case('Strings')
                                     <td><input type="text" wire:model.live="work_order_specs.{{$item->name}}" class="form-control"></td>
                                    @break

                                    @case('Options')
                                     <td>
                                         <select class="form-control" wire:model.live="work_order_specs.{{$item->name}}">
                                             <option>Choose..</option>
                                             @foreach(json_decode($item->value) as $option)
                                                 <option>{{ $option }}</option>
                                             @endforeach
                                         </select>
                                     </td>
                                    @break
                                    @case('Numbers')
                                    <td><input type="number" step="0.01" wire:model.live="work_order_specs.{{$item->name}}" class="form-control"></td>
                                    @break
                                @endswitch
                            </tr>
                        @endforeach
                    @endif
                </table>
                <button class="btn btn-primary" wire:click="store">@if(!$work_order) Create @else Update @endif Job</button>
            </div>
        </div>
    </div>

</div>
