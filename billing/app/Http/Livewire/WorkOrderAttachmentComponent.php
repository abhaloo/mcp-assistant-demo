<?php

namespace App\Http\Livewire;

use App\Models\WorkOrder;
use App\Models\WorkOrderAttachment;
use Illuminate\Support\Facades\Storage;
use Illuminate\Support\Str;
use Livewire\Component;
use Livewire\WithFileUploads;
use Livewire\WithPagination;

class WorkOrderAttachmentComponent extends Component
{
    use WithFileUploads, WithPagination;

    protected $paginationTheme = 'bootstrap';

    public $search_keyword = null;

    public $update = false;

    public $delete_confirm_id = null;

    public $delete_confirm_description = null;

    public $description;

    public $file = null;

    public $work_order_id;

    public function create()
    {
        $this->resetField();
    }

    public function download($attachmentId)
    {
        abort_unless(auth()->user()?->can('view job'), 403);

        $workOrder = $this->authorizedWorkOrder();
        $attachment = $this->authorizedAttachment($workOrder, $attachmentId);

        $disk = WorkOrderAttachment::diskName();

        if (Storage::disk($disk)->exists($attachment->path)) {
            return Storage::disk($disk)->download($attachment->path);
        }

        if (Storage::disk('private')->exists($attachment->path)) {
            return Storage::disk('private')->download($attachment->path);
        }

        abort(404);
    }

    public function store()
    {
        abort(403);

        $workOrder = $this->authorizedWorkOrder();

        $this->validate([
            'description' => 'required',
            'file' => 'required|mimes:pdf,jpg,png,jpeg,doc,docx,xls,xlsx,gif|max:5000',
        ]);

        $filename = Str::random(40).'.'.$this->file->extension();
        $disk = WorkOrderAttachment::diskName();
        $file_path = $this->file->storeAs('attachments', $filename, $disk);

        if (! Storage::disk($disk)->exists($file_path)) {
            $this->addError('file', 'The artwork file could not be saved. Please try again.');

            return;
        }

        WorkOrderAttachment::create([
            'work_order_id' => $workOrder->id,
            'description' => $this->description,
            'path' => $file_path,
            'extension' => $this->file->extension(),
            'size' => $this->file->getSize(),
            'user_id' => auth()->id(),
        ]);

        $this->dispatch('swal:info', title: 'success', text: 'Attachment Created');

        $this->resetField();

        $this->dispatch('closeModal');
    }

    public function deleteConfirm($attachmentId)
    {
        $workOrder = $this->authorizedWorkOrder();

        $attachment = $this->authorizedAttachment($workOrder, $attachmentId);
        $this->delete_confirm_id = (int) $attachmentId;
        $this->delete_confirm_description = $attachment->description;
    }

    public function destroy()
    {
        abort(403);

        $workOrder = $this->authorizedWorkOrder();

        abort_if($this->delete_confirm_id === null, 404);

        $attachment = $this->authorizedAttachment($workOrder, $this->delete_confirm_id);

        $attachment->delete();

        $this->delete_confirm_id = null;
        $this->delete_confirm_description = null;

        $this->dispatch('swal:info', title: 'success', text: 'Attachment Deleted');
    }

    private function authorizedWorkOrder(): WorkOrder
    {
        $query = WorkOrder::query()->whereKey($this->work_order_id);

        $user = auth()->user();

        abort_if($user === null, 403);

        if (! $user->hasRole('Super Admin')) {
            $query->where('entity_id', $user->entity_id);
        }

        $workOrder = $query->first();

        abort_if($workOrder === null, 404);

        return $workOrder;
    }

    private function authorizedAttachment(WorkOrder $workOrder, $attachmentId): WorkOrderAttachment
    {
        $attachment = WorkOrderAttachment::where('work_order_id', $workOrder->id)
            ->whereKey($attachmentId)
            ->first();

        abort_if($attachment === null, 404);

        return $attachment;
    }

    private function resetField()
    {
        $this->reset('description', 'file', 'update');
    }

    public function render()
    {
        $workOrder = $this->authorizedWorkOrder();

        $work_order_attachments = WorkOrderAttachment::query()
            ->where('work_order_id', $workOrder->id)
            ->latest();

        if ($this->search_keyword) {
            $work_order_attachments->where(function ($query) {
                $query->where('id', $this->search_keyword)
                    ->orWhere('description', 'like', '%'.$this->search_keyword.'%');
            });
        }

        $work_order_attachments = $work_order_attachments->paginate();

        return view('livewire.work-order-attachment-component', [
            'work_order_attachments' => $work_order_attachments,
        ]);
    }
}
