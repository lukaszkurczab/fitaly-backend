web: gunicorn -k uvicorn.workers.UvicornWorker -w 4 -b 0.0.0.0:$PORT --timeout 120 --graceful-timeout 30 --log-level info --access-logfile - --error-logfile - app.main:app
