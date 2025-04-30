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
-v $HOME/private/dmm.cfg:/opt/dmm/dmm.cfg \
-v $HOME/private/rucio.cfg:/opt/rucio/etc/rucio.cfg \
-v $HOME/private/certs/rucio-sense/:/opt/certs/ \
-v $HOME/.sense-o-auth.yaml:/root/.sense-o-auth.yaml \
-v /etc/grid-security/certificates/:/etc/grid-security/certificates \
--name dmm \
dmm-debug