<?php

use Illuminate\Support\Facades\Route;

Route::get('/', function () {
    return view('welcome');
});
Route::view('/about', 'about');
Route::get('/posts/{post}', function ($post) {
    return view('post', ['post' => $post]);
});
