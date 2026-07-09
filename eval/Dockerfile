FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app/eval

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./
RUN chmod +x run_eval.sh

CMD ["./run_eval.sh"]
