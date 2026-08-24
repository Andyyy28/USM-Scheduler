#!/bin/sh
set -eu

is_true() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

if is_true "${WAIT_FOR_DATABASE:-true}"; then
  echo "Waiting for the application database..."
  attempt=0
  until python - <<'PY'
import django
from django.db import connections

django.setup()
with connections["default"].cursor() as cursor:
    cursor.execute("SELECT 1")
    cursor.fetchone()
PY
  do
    attempt=$((attempt + 1))
    if [ "${attempt}" -ge "${DATABASE_WAIT_ATTEMPTS:-30}" ]; then
      echo "Database did not become ready in time." >&2
      exit 1
    fi
    sleep 2
  done
fi

if is_true "${RUN_MIGRATIONS:-false}"; then
  echo "Applying database migrations..."
  python manage.py migrate --noinput
fi

if is_true "${COLLECT_STATIC:-false}"; then
  echo "Collecting static assets..."
  python manage.py collectstatic --noinput --clear
fi

exec "$@"
