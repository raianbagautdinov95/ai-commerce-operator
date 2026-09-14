FROM postgres:18-alpine
RUN apk add --no-cache python3 py3-boto3
WORKDIR /opt/backup
COPY offsite.py /opt/backup/offsite.py
USER postgres
ENTRYPOINT ["python3", "/opt/backup/offsite.py"]
