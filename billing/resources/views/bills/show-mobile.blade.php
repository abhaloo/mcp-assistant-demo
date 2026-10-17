@extends('layouts.app')

@section('content')
    @livewire('show-bill-component', ['bill' => $bill->id, 'fromOrder' => $fromOrder ?? null, 'mobile' => true])
@endsection
