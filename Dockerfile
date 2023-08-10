FROM python:3.7-slim-bullseye

RUN pip3 install sense-o-api==1.23 sqlalchemy psycopg2-binary
EXPOSE 5000

WORKDIR /opt/dmm

ENV PYTHONPATH=/opt/dmm/

ENV DMM_HOST=127.0.0.1
ENV DMM_PORT=5000
ENV DMM_CONFIG /opt/dmm/dmm.cfg

ENTRYPOINT ["/bin/bash"]
