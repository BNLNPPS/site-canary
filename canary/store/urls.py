from django.urls import path

from . import views

app_name = 'canary'

urlpatterns = [
    path('', views.canary_page, name='canary_page'),
    path('probes/', views.probes_page, name='probes_page'),
    path('probes/config/', views.probe_config_update,
         name='probe_config_update'),
    path('probes/run-now/', views.probe_run_now, name='probe_run_now'),
    path('probes/<str:queue_name>/runs/', views.probe_runs_page,
         name='probe_runs'),
]
