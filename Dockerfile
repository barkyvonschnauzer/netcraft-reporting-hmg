# Official image used as part of parent image
FROM python:3-slim-buster

COPY requirements.txt .
COPY netcraft_submission.py .

RUN apt-get update && pip install --no-cache-dir -r requirements.txt

CMD [ "python", "netcraft_submission.py" ]
