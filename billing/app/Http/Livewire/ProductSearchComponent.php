<?php

namespace App\Http\Livewire;

use App\Models\Product;
use Livewire\Component;

class ProductSearchComponent extends Component
{
    public $query;

    public $products;

    public $highlightIndex;

    public $type;

    public function mount($type = 'Product & Service')
    {
        $this->type = $type;
        if ($type == 'Product & Service') {
            $this->type = ['Product', 'Service'];
        }

        if ($type == 'Product & Material') {
            $this->type = ['Product', 'Material'];
        }

        $this->resetFields();
    }

    public function resetFields()
    {
        $this->query = '';
        $this->products = [];
        $this->highlightIndex = 0;
    }

    public function incrementHighlight()
    {
        if ($this->highlightIndex === count($this->products) - 1) {
            $this->highlightIndex = 0;

            return;
        }
        $this->highlightIndex++;
    }

    public function decrementHighlight()
    {
        if ($this->highlightIndex === 0) {
            $this->highlightIndex = count($this->products) - 1;

            return;
        }
        $this->highlightIndex--;
    }

    public function selectProduct($product = null)
    {
        if (! $product) {
            $product = $this->products[$this->highlightIndex] ?? null;
        } else {
            $product = Product::find($product)->toArray();
        }

        if ($product) {
            $this->dispatch('itemAdded', item_id: $product['id']);
            $this->resetFields();
        }

    }

    public function updatedQuery()
    {
        $query = Product::with(['unit', 'adjustedItems', 'receivedItems', 'billItems', 'issuedItems'])
            ->where(function ($q) {
                $q->where('name', 'like', "%{$this->query}%")
                    ->orWhere('sku', $this->query);
            });

        if (is_array($this->type)) {
            $query->whereIn('type', $this->type);
        } else {
            $query->where('type', $this->type);
        }

        $this->products = $query->get()->map(function ($product) {
            $arr = $product->toArray();
            $arr['stock_label'] = $product->stock_label;

            return $arr;
        })->toArray();
    }

    public function render()
    {
        return view('livewire.product-search-component');
    }
}
