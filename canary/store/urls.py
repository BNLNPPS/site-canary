from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from . import views

app_name = 'canary'

urlpatterns = [
    path('', views.canary_page, name='canary_page'),
    path('probes/', views.probes_page, name='probes_page'),
    path('probes/config/', views.probe_config_update,
         name='probe_config_update'),
    path('probes/run-now/', views.probe_run_now, name='probe_run_now'),
    path('probes/api/config/', csrf_exempt(views.probe_config_api),
         name='probe_config_api'),
    path('probes/api/run-now/', csrf_exempt(views.probe_run_now_api),
         name='probe_run_now_api'),
    path('probes/api/payload-canary/', csrf_exempt(views.payload_canary_api),
         name='payload_canary_api'),
    path('probes/payload-canary/', views.payload_canary_run_now,
         name='payload_canary_run_now'),
    path('probes/<str:queue_name>/runs/', views.probe_runs_page,
         name='probe_runs'),
]
