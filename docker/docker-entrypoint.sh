#!/bin/bash
set -e

config=${DMM_CONFIG:-/opt/dmm/dmm.cfg}

db_host=$(grep "db_host" "$config" | cut -d '=' -f2)
db_port=$(grep "db_port" "$config" | cut -d '=' -f2)

if [[ -z "$db_host" || -z "$db_port" ]]; then
    echo "No db_host/db_port in $config, cannot wait for the database" >&2
    exit 1
fi

# Long enough for a database in a pod of its own to come up cold: scheduling, a
# volume attach and initdb on a fresh PVC all happen before it accepts a
# connection, which is well past wait-for-it's 15s default. Failing here rather
# than letting dmm start is deliberate - it faults on the first query anyway, and
# this says why.
db_wait_timeout=${DMM_DB_WAIT_TIMEOUT:-300}

echo "Waiting up to ${db_wait_timeout}s for DB at ${db_host}:${db_port}"
/wait-for-it.sh -h "$db_host" -p "$db_port" -t "$db_wait_timeout"
echo "Done..."

dmm
