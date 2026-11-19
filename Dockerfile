FROM python:3.12-slim
WORKDIR /app
COPY disaster_alert_bot.py .
ENV PYTHONUNBUFFERED=1 STATE_FILE=/data/state.json
VOLUME ["/data"]
CMD ["python", "disaster_alert_bot.py"]
