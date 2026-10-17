@extends('layouts.app')

@section('content')
    @livewire('inventory-editor-component', ['inventory_id' => $inventory->id, 'job_number' => $job_number])
@endsection
