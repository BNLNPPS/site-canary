from django.urls import path

from . import views

app_name = 'canary'

urlpatterns = [
    path('', views.canary_page, name='canary_page'),
]
