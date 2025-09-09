#!/bin/bash

db_host=$(grep "db_host" /opt/dmm/dmm.cfg | cut -d '=' -f2)
db_port=$(grep "db_port" /opt/dmm/dmm.cfg | cut -d '=' -f2)

echo "Waiting for DB to be ready"
/wait-for-it.sh -h $db_host -p $db_port
echo "Done..."

dmm
