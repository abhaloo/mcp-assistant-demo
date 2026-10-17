@extends('layouts.app')

@section('content')
    @livewire('bill-editor-component', [$bill->id, $type])
@endsection
