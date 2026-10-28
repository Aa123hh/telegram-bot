FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt
COPY bot.py core.py /app/
USER 65532:65532
CMD ["python", "-u", "bot.py"]
