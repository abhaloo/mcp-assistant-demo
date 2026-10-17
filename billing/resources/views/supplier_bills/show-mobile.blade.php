@extends('layouts.app')

@section('content')
    @livewire('supplier-bill-show-component', ['inventory' => $inventory->id, 'mobile' => true])
@endsection
