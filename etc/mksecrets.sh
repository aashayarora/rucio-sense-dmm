#!/bin/bash

kubectl delete secret dmm-certs -n ucsd-rucio
kubectl delete secret dmm-config -n ucsd-rucio
kubectl delete secret sense-config -n ucsd-rucio
kubectl delete secret rucio-client-config -n ucsd-rucio

kubectl create secret generic dmm-certs --from-file=$HOME/private/certs/rucio-sense/hostcert.pem --from-file=$HOME/private/certs/rucio-sense/hostcert.key.pem -n ucsd-rucio
kubectl create secret generic dmm-config --from-file=$HOME/private/dmm.cfg -n ucsd-rucio
kubectl create secret generic rucio-client-config --from-file=$HOME/private/rucio.cfg -n ucsd-rucio
kubectl create secret generic sense-config --from-file $HOME/.sense-o-auth.yaml -n ucsd-rucio
