from django.urls import path

from scheduler import views

app_name = "scheduler"

urlpatterns = [
    path("healthz/", views.healthz, name="healthz"),
    path("", views.dashboard, name="dashboard"),
    path("terms/", views.terms, name="terms"),
    path("runs/compare/", views.run_comparison, name="run-comparison"),
    path("experiments/<str:pk>/", views.experiment_detail, name="experiment-detail"),
    path("runs/<str:pk>/", views.run_detail, name="run-detail"),
    path("runs/", views.runs, name="runs"),
    path("schedules/", views.schedules, name="schedules"),
    path("imports/", views.imports, name="imports"),
    path("reviews/", views.reviews, name="reviews"),
    path("help/", views.help_guide, name="help"),
]
