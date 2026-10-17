@extends('layouts.app')

@section('content')
    @livewire('specification-item-component', ['specification' => $specification])
@endsection
