<?php

namespace App\Http\Livewire;

use App\Models\WorkOrder;
use Livewire\Attributes\On;
use Livewire\Component;

class JobSearchComponent extends Component
{
    public $query = '';

    public $jobs = [];

    public $selectedJob = null;

    // Config
    public $label = 'Job';

    public $placeholder = 'Search job by number or title...';

    public $statusFilter = null;

    public $initialJobId = null;

    public function mount($label = null, $placeholder = null, $statusFilter = null, $initialJobId = null)
    {
        if ($label) {
            $this->label = $label;
        }
        if ($placeholder) {
            $this->placeholder = $placeholder;
        }
        $this->statusFilter = $statusFilter;

        if ($initialJobId) {
            $job = WorkOrder::find($initialJobId);
            if ($job) {
                $this->selectedJob = $job->toArray();
                $this->query = $job->work_number.' - '.$job->title;
                $this->dispatch('jobSelected', jobId: $job->id);
            }
        }
    }

    #[On('resetJobSearch')]
    public function resetFields()
    {
        $this->query = '';
        $this->jobs = [];
        $this->selectedJob = null;
    }

    public function selectJob($jobId)
    {
        $job = WorkOrder::find($jobId);

        if ($job) {
            $this->selectedJob = $job->toArray();
            $this->query = $job->work_number.' - '.$job->title;
            $this->jobs = [];
            $this->dispatch('jobSelected', jobId: $job->id);
        }
    }

    public function clearSelection()
    {
        $this->query = '';
        $this->selectedJob = null;
        $this->jobs = [];
        $this->dispatch('jobCleared');
    }

    public function closeDropdown()
    {
        $this->jobs = [];
    }

    public function updatedQuery()
    {
        if (empty($this->query)) {
            $this->jobs = [];
            $this->selectedJob = null;

            return;
        }

        $this->selectedJob = null;

        $query = WorkOrder::query()
            ->where(function ($q) {
                $q->where('work_number', 'like', "%{$this->query}%")
                    ->orWhere('title', 'like', "%{$this->query}%");
            });

        if ($this->statusFilter) {
            $query->where('status', $this->statusFilter);
        }

        $this->jobs = $query
            ->orderBy('created_at', 'desc')
            ->limit(10)
            ->get()
            ->map(function ($job) {
                return [
                    'id' => $job->id,
                    'work_number' => $job->work_number,
                    'title' => $job->title,
                    'status' => $job->status,
                ];
            })
            ->toArray();
    }

    public function render()
    {
        return view('livewire.job-search-component');
    }
}
