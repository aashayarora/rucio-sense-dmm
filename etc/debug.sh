#!/bin/bash

echo "Starting DMM development environment"
docker build . -t dmm-debug 

# if --rm passed then trap docker rm postgres
# trap "docker stop postgres" EXIT

echo "Starting Postgres"
docker run -d --rm \
--network host \
--name postgres \
-e POSTGRES_USER=dmm \
-e POSTGRES_PASSWORD=dmm \
postgres

echo "Starting DMM"
docker run -it --rm \
--network host \
--add-host nrp-01.nrp-nautilus.io:127.0.0.1 \
-v $HOME/dmm.cfg:/opt/dmm/dmm.cfg \
-v $HOME/rucio.cfg:/opt/rucio/etc/rucio.cfg \
-v $HOME/private/certs/rucio-sense/:/opt/certs/ \
-v $HOME/.sense-o-auth.yaml:/root/.sense-o-auth.yaml \
-v /etc/grid-security/certificates/:/etc/grid-security/certificates \
--name dmm \
dmm-debug
