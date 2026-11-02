from django.shortcuts import render


def home(request):
    return render(request, "home.html")


def menu(request):
    return render(request, "menu.html")


def dish(request, slug):
    return render(request, "dish.html", {"slug": slug})
