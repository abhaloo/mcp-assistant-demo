@extends('layouts.app')

@section('content')
    @livewire('inventory-show-component', [$inventory->id])
@endsection
