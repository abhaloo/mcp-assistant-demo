@extends('layouts.app')

@section('content')
    @livewire('supplier-bill-editor-component', [$inventory->id])
@endsection
