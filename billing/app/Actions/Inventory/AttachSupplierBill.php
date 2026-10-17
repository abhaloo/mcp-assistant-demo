<?php

namespace App\Actions\Inventory;

use App\Models\Inventory;
use Illuminate\Database\Eloquent\Collection;
use InvalidArgumentException;

class AttachSupplierBill
{
    /**
     * Attach a supplier bill to a receive entry.
     *
     * @param  Inventory  $receiveEntry  The receive entry (type='Receive')
     * @param  Inventory  $supplierBill  The supplier bill (type='Bill')
     *
     * @throws InvalidArgumentException
     */
    public static function handle(Inventory $receiveEntry, Inventory $supplierBill): void
    {
        // Validate receive entry
        if ($receiveEntry->type !== 'Receive') {
            throw new InvalidArgumentException('First parameter must be a Receive entry');
        }

        // Validate supplier bill
        if ($supplierBill->type !== 'Bill') {
            throw new InvalidArgumentException('Second parameter must be a Supplier Bill');
        }

        // Validate supplier match
        if ($receiveEntry->supplier_id !== $supplierBill->supplier_id) {
            throw new InvalidArgumentException(
                'Supplier mismatch: Receive entry supplier does not match bill supplier'
            );
        }

        // Attach
        $receiveEntry->update([
            'supplier_bill_id' => $supplierBill->id,
        ]);
    }

    /**
     * Detach supplier bill from receive entry.
     */
    public static function detach(Inventory $receiveEntry): void
    {
        if ($receiveEntry->type !== 'Receive') {
            throw new InvalidArgumentException('Parameter must be a Receive entry');
        }

        $receiveEntry->update([
            'supplier_bill_id' => null,
        ]);
    }

    /**
     * Get available supplier bills for attachment.
     * Returns bills that match the supplier and are not already attached.
     *
     * @return Collection
     */
    public static function getAvailableBills(Inventory $receiveEntry)
    {
        if (! $receiveEntry->supplier_id) {
            return collect([]);
        }

        return Inventory::where('type', 'Bill')
            ->where('supplier_id', $receiveEntry->supplier_id)
            ->where('status', 'APPROVED')
            ->where(function ($query) use ($receiveEntry) {
                // Not attached to any receive entry, OR already attached to this one
                $query->whereDoesntHave('linkedReceiveEntries')
                    ->orWhereHas('linkedReceiveEntries', function ($q) use ($receiveEntry) {
                        $q->where('id', $receiveEntry->id);
                    });
            })
            ->orderBy('inventory_date', 'desc')
            ->get();
    }

    /**
     * Validate if a bill can be attached to a receive entry.
     *
     * @return array ['valid' => bool, 'message' => string]
     */
    public static function validate(Inventory $receiveEntry, Inventory $supplierBill): array
    {
        if ($receiveEntry->type !== 'Receive') {
            return ['valid' => false, 'message' => 'Not a valid receive entry'];
        }

        if ($supplierBill->type !== 'Bill') {
            return ['valid' => false, 'message' => 'Not a valid supplier bill'];
        }

        if ($receiveEntry->supplier_id !== $supplierBill->supplier_id) {
            return ['valid' => false, 'message' => 'Supplier mismatch'];
        }

        if ($supplierBill->status !== 'APPROVED') {
            return ['valid' => false, 'message' => 'Supplier bill is not approved'];
        }

        // Check if bill is already attached to another receive entry
        $existingAttachment = Inventory::where('supplier_bill_id', $supplierBill->id)
            ->where('id', '!=', $receiveEntry->id)
            ->first();

        if ($existingAttachment) {
            return [
                'valid' => false,
                'message' => 'Bill is already attached to receive entry #'.$existingAttachment->id,
            ];
        }

        return ['valid' => true, 'message' => 'OK'];
    }
}
