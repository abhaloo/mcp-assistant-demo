@extends('layouts.app')

@section('content')
    @livewire('customer-profile-component', ['customer' => $customer])
@endsection
