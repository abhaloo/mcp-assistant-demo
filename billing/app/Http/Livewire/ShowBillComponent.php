<?php

namespace App\Http\Livewire;

use App\Actions\Entries\PostEntry;
use App\Authorization\BusinessRecordRegistry;
use App\Helpers\BadgeHelper;
use App\Models\Account;
use App\Models\Bill;
use App\Models\BillItem;
use App\Models\CreditNote;
use App\Support\BulkActivityLogger;
use App\Support\LookupCache;
use Livewire\Component;

class ShowBillComponent extends Component
{
    public $bill;

    public $fromOrder = null;

    public $mobile = false;

    /**
     * Task P7 — the authorized Bill subtype ('invoice'|'quotation'|
     * 'payable_quotation'), resolved once in mount() via the same registry
     * predicate match RecordContextResolver/AiAccessPolicy already use. Null
     * for an unrecognized subtype (e.g. type='Payable Quotation' but
     * payable='No') — the "Ask about this bill" button never renders then.
     */
    public ?string $recordResourceType = null;

    /** Task P7 — services.rag.enabled, read once at mount (config, not live health). */
    public bool $ragEnabled = false;

    /** Task P7 — services.rag.record_context, read once at mount. */
    public bool $recordContextEnabled = false;

    public $method;

    public $paid_date;

    public $amount;

    public $description;

    public $status = null;

    public $bill_value;

    public $tax_value;

    public $balance = 0;

    public $quantity_returned = [];

    public $credit_note_method;

    public $amount_to_return = null;

    public $amount_returned = 0;

    protected $rules = [
        'method' => 'required|numeric',
        'paid_date' => 'required|date',
        'amount' => 'required|numeric',
        'description' => 'nullable|string',
    ];

    public function store()
    {
        abort(403);
        $this->validate();

        if ($this->amount > $this->bill->balance()) {
            $this->dispatch('swal:info', title: 'error', text: 'Amount is greater than balance');

            return;
        }

        $receivable = Account::where('account_type', Account::RECEIVABLE)
            ->where('isSystemAccount', true)->first();
        // add payment
        PostEntry::handle(
            $this->amount,
            $this->method,
            $receivable->id,
            'Payment for Invoice #'.$this->bill->id.' '.$this->description,
            $this->paid_date,
            bill_id: $this->bill->id,
            transaction_type: 'Customer Payment'
        );

        // Reload the bill (and its cached payments relation) so the same-request
        // re-render reflects the new balance/status instead of pre-payment state.
        $this->bill->refresh();

        $this->reset('method', 'paid_date', 'amount', 'description');

        $this->dispatch('swal:info', title: 'success', text: 'Payment Added');

        // close modal
        $this->dispatch('closeModal');
    }

    public function mount(Bill $bill, $fromOrder = null, $mobile = false)
    {
        $bill->load('controlNumber');
        $this->bill = $bill;
        $this->fromOrder = $fromOrder;
        $this->mobile = $mobile;

        $this->recordResourceType = app(BusinessRecordRegistry::class)->resourceTypeForModel($bill);
        $this->ragEnabled = (bool) config('services.rag.enabled', false);
        $this->recordContextEnabled = (bool) config('services.rag.record_context', false);

        foreach ($bill->billItems as $billItem) {
            $this->quantity_returned[$billItem->id] = [
                'id' => $billItem->id,
                'quantity_returned' => $billItem->quantity_returned,
            ];
        }
    }

    public function billValue()
    {
        $this->bill_value = $this->bill->billItems->sum(function ($item) {
            return (($item->quantity - $item->quantity_returned) * $item->price) - $item->discount;
        });

        $this->tax_value = $this->bill->billItems->sum(function ($item) {
            $lineTotal = (($item->quantity - $item->quantity_returned) * $item->price) - $item->discount;

            return $lineTotal * ($item->tax / 100);
        });

        $this->balance = $this->bill->balance();

        if ($this->balance == 0) {
            $this->status = BadgeHelper::render('PAID', 'success');
        } elseif ($this->balance > 0 && $this->balance != $this->bill->amount()) {
            $this->status = BadgeHelper::render('PARTIAL PAID', 'sky');
        } else {
            $this->status = BadgeHelper::render('UNPAID', 'danger');
        }
    }

    public function storeCreditNote()
    {
        abort(403);
        $this->validate([
            'credit_note_method' => 'required',
        ], [
            'credit_note_method.required' => 'How you returned the money ?',
        ]);

        foreach ($this->quantity_returned as $item) {
            $billItem = BillItem::find($item['id']);
            if ($billItem) {
                $billItem->quantity_returned = $item['quantity_returned'];
                $billItem->save();
            }
        }

        BulkActivityLogger::log(
            'Credit note quantities recorded',
            $this->bill,
            ['items' => $this->quantity_returned]
        );

        // add Reverse Payment / Refund
        //        $payment = Payment::create([
        //            "payment_date" => today()->format('Y-m-d'),
        //            "provider_id" => $this->credit_note_method,
        //            "bill_id" => $this->bill->id,
        //            "receipt" => rand(12344,45622),
        //            "payment_reference" => 'm'.rand(12344,45622),
        //            "transaction_reference" => 'm'.rand(12344,45622),
        //            "description" => 'Credit Note Refund',
        //            "amount" => $this->amount_returned * -1,
        //        ]);

        // Add Credit Note
        CreditNote::create([
            'bill_id' => $this->bill->id,
            'payment_id' => $payment->id,
        ]);

        $this->dispatch('swal:info', title: 'success', text: 'Credit Note Created');

        $this->bill->refresh();

        // close modal
        $this->dispatch('closeModalCreditNote');
    }

    public function removeCreditNote(CreditNote $creditNote)
    {
        abort(403);
        // reset bill item
        foreach ($creditNote->bill->billItems as $billItem) {
            $billItem->quantity_returned = 0;
            $billItem->save();
        }

        BulkActivityLogger::log(
            'Credit note removed — bill item return quantities reset',
            $creditNote->bill
        );

        // remove payment
        // Payment::where('id', $creditNote->payment_id)->delete();

        $creditNote->delete();

        $this->dispatch('swal:info', title: 'success', text: 'Credit Note Removed');

        $this->bill->refresh();

        // close modal
        $this->dispatch('closeModalCreditNote');

    }

    public function render()
    {
        $this->bill = $this->bill->load(['controlNumber', 'creditNote', 'billItems']);
        $this->billValue();

        $billItemsById = $this->bill->billItems->keyBy('id');

        // amount to return
        $this->amount_to_return = 0;
        foreach ($this->quantity_returned as $item) {
            $billItem = $billItemsById->get($item['id']);

            if ($billItem === null) {
                continue;
            }

            $this->amount_to_return = $this->amount_to_return + ($billItem->price - $billItem->discount)
                * intval($item['quantity_returned']);
        }

        $providers = LookupCache::remember(
            'accounts.bank_cash',
            fn () => Account::whereIn('account_type', [Account::BANK, Account::CASH])->get()
        );

        $view = $this->mobile ? 'livewire.show-bill-mobile-component' : 'livewire.show-bill-component';

        return view($view, compact('providers'));
    }
}
