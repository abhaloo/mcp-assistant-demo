{{-- Shared print/PDF export view for work orders --}}
{{-- Used by both desktop (work-data-component) and mobile (work-data-mobile) --}}
<div class="row me-1" id="printer" style="display: none;">
    <style>
        #printer {
            font-size: 8.8pt;
            padding-left: 10px;
            margin-top: 0 !important;
            padding-top: 0 !important;
        }

        .input-table {
            width: 100%;
        }

        .input-table td {
            text-align: right;
        }

        .input-table tr:hover {
            background: #C3E88D;
        }

        .input-options {
            display: inline-block;
            width: auto;
        }

        /* Checkbox styling for PDF export - uses actual elements instead of pseudo-elements */
        .checkbox-box {
            display: inline-block;
            width: 16px;
            height: 16px;
            border: 1px solid #0c4128;
            vertical-align: middle;
            margin-left: 4px;
        }

        .checkbox-unchecked {
            background: #fff;
        }

        .checkbox-checked {
            background: #dc3545;
        }

        .input-title {
            text-align: left !important;
            color: #022925;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            display: block;
            width: fit-content;
            min-width: 1px;
            border-bottom: #00c4a7 dotted 1px;
        }

        .input-title-sub {
            text-align: right;
        }

        .input-box {
            padding-left: 3px;
            width: 100%;
            min-width: fit-content;
            border-width: 0;
            border-bottom: #0a0a0a solid 1px;
        }

        .input-box:focus {
            background: #e4f5cc;
            border: #C3E88D solid 1px !important;
        }

        .row-spacing {
            margin-top: 0.3rem !important;
            margin-bottom: 0.3rem !important;
        }

        /* Grid system for PDF export - ensures html2canvas captures column layout */
        #printer .row::after { content: ""; display: table; clear: both; }
        #printer [class*="col-"] { float: left; box-sizing: border-box; }
        #printer .col-1 { width: 8.33%; }
        #printer .col-2 { width: 16.66%; }
        #printer .col-3 { width: 25%; }
        #printer .col-4 { width: 33.33%; }
        #printer .col-5 { width: 41.66%; }
        #printer .col-6 { width: 50%; }
        #printer .col-7 { width: 58.33%; }
        #printer .col-8 { width: 66.66%; }
        #printer .col-9 { width: 75%; }
        #printer .col-10 { width: 83.33%; }
        #printer .col-11 { width: 91.66%; }
        #printer .col-12 { width: 100%; }

        /* Compact spacing to fit on single page */
        #printer hr { margin: 2px 0; }
        #printer .my-1 { margin-top: 2px !important; margin-bottom: 2px !important; }
        #printer .mt-1 { margin-top: 2px !important; }
        #printer .mt-3 { margin-top: 6px !important; }
        #printer .mb-1 { margin-bottom: 2px !important; }
        #printer p { margin: 2px 0; }
    </style>
    <div class="col-md-12">
        <table style="width: 100%">
            <tr>
                <td>
                    <img src="{{ asset('img/logo/logo.gif') }}" width="120">
                </td>
                <td style="text-align: right">
                    <strong>Job #:</strong>{{$workOrder->work_number ? : $workOrder->id}}<br/>
                    <strong>Bill
                        #:</strong>{{!$workOrder->bill ? 'None':($workOrder->bill->invoice_number ?? $workOrder->bill->id)}}
                    |
                    <strong>Created At:</strong>{{$workOrder->created_at->format('d F Y')}}
                    <strong>Created By:</strong>{{$workOrder->user->name ?? 'N/A'}}
                </td>
            </tr>
        </table>
    </div>
    <hr/>
    <div class="col-12 my-1">
        Customer Name: <span class="me-2 text-decoration-underline fw-bold">{{ $workOrder->customer->name ?? 'NIL' }}</span>
    </div>
    <div class="col-12 my-1">
        <table class="input-table">
            <tr>
                <td class="input-title input-title-sub" style="width: auto;">Job Description:</td>
                <td class="value" style="text-align: left !important;width: 100%">
                    <input type="text" value="{{ $title }}" class="input-box" readonly></td>
            </tr>
        </table>
    </div>
    <div class="col-4 my-1">
        <table class="input-table">
            <tr>
                <td class="input-title input-title-sub"> Ordered Quantity:</td>
                <td class="value" style="text-align: left !important;width: 100%">
                    <input type="text" value="{{ $ordered_qty }}" class="input-box" readonly></td>
            </tr>
        </table>
    </div>
    <div class="col-4 my-1">
        <table class="input-table">
            <tr>
                <td class="input-title input-title-sub"> No of Pages:</td>
                <td class="value" style="text-align: left !important;width: 100%">
                    <input type="text" value="{{ $no_pages }}" class="input-box" readonly></td>
            </tr>
        </table>
    </div>
    @php
        $sortedItems = $workOrder->specification->items->sortBy('sort_order');
        $grouped = $sortedItems->groupBy(function($item) {
            return $item->group_name ?? '';
        });
        $ungrouped = $grouped->get('', collect());
        $grouped = $grouped->forget('');
    @endphp

    {{-- Render ungrouped items first --}}
    <div class="row">
        @foreach($ungrouped as $item)
            @include('livewire.partials.print-field', ['item' => $item, 'payload' => $payload])
        @endforeach
    </div>

    {{-- Render grouped items with section headers --}}
    @foreach($grouped as $groupName => $items)
        <div class="col-12 mt-3 mb-1">
            <strong class="text-uppercase" style="border-bottom: 2px solid #333; padding-bottom: 2px; display: inline-block;">
                {{ $groupName }}
            </strong>
        </div>
        <div class="row">
            @foreach($items as $item)
                @include('livewire.partials.print-field', ['item' => $item, 'payload' => $payload])
            @endforeach
        </div>
    @endforeach

    {{-- To be hand written section (PDF only) --}}
    <div class="col-12 mt-1">
        <hr>
        <p><em>To be hand written</em></p>
        <table class="input-table" style="width: 100%">
            <tr>
                <td class="input-title-sub" style="width: 130px;">Charge by Invoice No:</td>
                <td class="value" style="text-align: left !important;">
                    <span class="input-box" style="display: inline-block; width: 120px;"></span>
                </td>
                <td class="input-title-sub" style="width: 60px;">D.O No:</td>
                <td class="value" style="text-align: left !important;">
                    <span class="input-box" style="display: inline-block; width: 120px;"></span>
                </td>
                <td class="input-title-sub" style="width: 50px;">Dated:</td>
                <td class="value" style="text-align: left !important;">
                    <span class="input-box" style="display: inline-block; width: 100px;"></span>
                </td>
            </tr>
            <tr style="margin-top: 10px;">
                <td class="input-title-sub" style="width: 130px;">Charge by Name:</td>
                <td class="value" style="text-align: left !important;">
                    <span class="input-box" style="display: inline-block; width: 120px;"></span>
                </td>
                <td class="input-title-sub" style="width: 60px;">Sign:</td>
                <td class="value" style="text-align: left !important;">
                    <span class="input-box" style="display: inline-block; width: 120px;"></span>
                </td>
                <td class="input-title-sub" style="width: 50px;">Dated:</td>
                <td class="value" style="text-align: left !important;">
                    <span class="input-box" style="display: inline-block; width: 100px;"></span>
                </td>
            </tr>
        </table>
    </div>

    <div id="output"></div>
</div>
