@extends('layouts.app')

@section('content')
    @livewire('bill-component', ['type' => $type])
@endsection
