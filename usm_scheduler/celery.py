import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "usm_scheduler.settings")

app = Celery("usm_scheduler")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
