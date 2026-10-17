@extends('layouts.app')

@section('content')
    @livewire('supplier-profile-component', ['supplier' => $supplier])
@endsection
