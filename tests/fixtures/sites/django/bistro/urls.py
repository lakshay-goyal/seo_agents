from django.urls import path

from menu import views

urlpatterns = [
    path("", views.home, name="home"),
    path("menu/", views.menu, name="menu"),
    path("dishes/<slug:slug>/", views.dish, name="dish"),
]
