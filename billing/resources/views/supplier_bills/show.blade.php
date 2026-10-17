@extends('layouts.app')

@section('content')
    @livewire('supplier-bill-show-component', [$inventory->id])
@endsection
