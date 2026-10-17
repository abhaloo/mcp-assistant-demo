@extends('layouts.app')

@section('content')
    @livewire('customer-order-show-component', ['customerOrder' => $customerOrder])
@endsection

