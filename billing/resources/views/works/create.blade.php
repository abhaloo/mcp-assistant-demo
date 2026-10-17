@extends('layouts.app')

@section('content')
    @livewire('work-order-editor-component', ['bill' => $bill, 'workOrder' => $workOrder])
@endsection
