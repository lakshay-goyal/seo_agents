<!doctype html>
<html lang="en">
<head>
    <title>@yield('title', 'Lumen Labs')</title>
    <meta name="description" content="@yield('description', 'Applied research lab.')">
</head>
<body>
    @include('partials.nav')
    <main>@yield('content')</main>
</body>
</html>
