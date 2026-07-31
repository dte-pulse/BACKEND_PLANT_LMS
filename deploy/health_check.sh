#!/bin/bash
set -e

echo "Running health checks..."

# Check Gunicorn socket
if [ -S /run/plant_lms.sock ]; then
  echo "Gunicorn socket is active."
else
  echo "ERROR: Gunicorn socket not found!"
  exit 1
fi

# Check if Nginx is running
if systemctl is-active --quiet nginx; then
  echo "Nginx is running."
else
  echo "ERROR: Nginx is not running!"
  exit 1
fi

# Check if Celery is running
if systemctl is-active --quiet celery; then
  echo "Celery worker is running."
else
  echo "ERROR: Celery is not running!"
  exit 1
fi

# Simple API health check
HTTP_STATUS=$(curl -o /dev/null -s -w "%{http_code}\n" http://localhost/api/v1/)
if [ "$HTTP_STATUS" -eq 200 ] || [ "$HTTP_STATUS" -eq 401 ] || [ "$HTTP_STATUS" -eq 404 ]; then
  echo "API is responding ($HTTP_STATUS)."
else
  echo "ERROR: API is not responding correctly! Status: $HTTP_STATUS"
  exit 1
fi

echo "All health checks passed!"
exit 0
