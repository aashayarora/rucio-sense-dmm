FROM python:3.7-slim-bullseye

RUN apt update && \
    apt-get clean autoclean && \
    apt-get autoremove --yes && \
    rm -rf /var/lib/{apt,dpkg,cache,log}/

COPY . /opt/dmm/
RUN pip3 install /opt/dmm/

COPY ./docker/wait-for-it.sh /wait-for-it.sh
COPY ./docker/docker-entrypoint.sh /docker-entrypoint.sh

ENV PYTHONPATH=/opt/dmm/
ENV DMM_CONFIG /opt/dmm/dmm.cfg

EXPOSE 80

ENTRYPOINT ["/docker-entrypoint.sh"]
