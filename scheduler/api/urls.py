from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    path("health/", views.HealthView.as_view(), name="health"),
    path("terms/", views.TermListView.as_view(), name="terms"),
    path("terms/<int:term_id>/revisions/", views.RevisionListView.as_view(), name="revisions"),
    path("revisions/<int:revision_id>/clone-term/", views.TermCloneView.as_view(), name="term-clone"),
    path("revisions/<int:revision_id>/finalize/", views.RevisionFinalizeView.as_view(), name="revision-finalize"),
    path("objective-profiles/", views.ObjectiveProfileListView.as_view(), name="objective-profiles"),
    path("imports/template/", views.ImportTemplateView.as_view(), name="import-template"),
    path("imports/preview/", views.ImportPreviewView.as_view(), name="import-preview"),
    path("imports/<int:batch_id>/commit/", views.ImportCommitView.as_view(), name="import-commit"),
    path("snapshots/", views.SnapshotListCreateView.as_view(), name="snapshots"),
    path("snapshots/<int:snapshot_id>/manifest/", views.SnapshotManifestView.as_view(), name="snapshot-manifest"),
    path("experiments/", views.ExperimentListCreateView.as_view(), name="experiments"),
    path("experiments/<int:experiment_id>/", views.ExperimentDetailView.as_view(), name="experiment-detail"),
    path("experiments/<int:experiment_id>/queue/", views.ExperimentQueueView.as_view(), name="experiment-queue"),
    path("experiments/<int:experiment_id>/cancel/", views.ExperimentCancelView.as_view(), name="experiment-cancel"),
    path(
        "experiments/<int:experiment_id>/export/<str:export_format>/",
        views.ExperimentExportView.as_view(),
        name="experiment-export",
    ),
    path("runs/", views.RunListCreateView.as_view(), name="runs"),
    path("runs/compare/", views.RunComparisonView.as_view(), name="run-comparison"),
    path("runs/<int:run_id>/", views.RunDetailView.as_view(), name="run-detail"),
    path("runs/<int:run_id>/cancel/", views.RunCancelView.as_view(), name="run-cancel"),
    path("schedules/", views.ScheduleListView.as_view(), name="schedules"),
    path("schedules/<int:schedule_id>/", views.ScheduleDetailView.as_view(), name="schedule-detail"),
    path("schedules/<int:schedule_id>/validate/", views.ScheduleValidateView.as_view(), name="schedule-validate"),
    path("schedules/<int:schedule_id>/submit-review/", views.ScheduleSubmitReviewView.as_view(), name="schedule-submit-review"),
    path("schedules/<int:schedule_id>/reviews/", views.ScheduleReviewView.as_view(), name="schedule-review"),
    path("schedules/<int:schedule_id>/approve/", views.ScheduleApproveView.as_view(), name="schedule-approve"),
    path("schedules/<int:schedule_id>/locks/", views.ScheduleLockView.as_view(), name="schedule-lock"),
    path(
        "schedules/<int:schedule_id>/regenerate/",
        views.ScheduleRegenerateView.as_view(),
        name="schedule-regenerate",
    ),
    path(
        "schedules/<int:schedule_id>/export/<str:export_format>/",
        views.ScheduleExportView.as_view(),
        name="schedule-export",
    ),
]
