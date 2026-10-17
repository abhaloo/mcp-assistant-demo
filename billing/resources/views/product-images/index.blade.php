@extends('layouts.app')

@section('content')
    @livewire('product-image-component', ['product' => $product])
@endsection
