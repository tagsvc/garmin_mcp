#!/bin/sh
# Entrypoint for the remote server image.
#
# Railway mounts volumes as root at container start (and only at start — never
# at build time), so the image cannot simply declare `USER appuser`: the server
# would then be unable to write /data. Instead we start as root, take ownership
# of the data directory, and drop to an unprivileged user before exec'ing the
# server. The long-lived process therefore never runs as root, which is the
# point: an RCE in the Python process does not get root in the container.
#
# If the container is already started as non-root (e.g. a local `docker run
# --user`), we skip straight to exec and assume /data is writable.
set -e

if [ "$(id -u)" = "0" ]; then
    mkdir -p /data/garmin_sessions
    # Only chown when needed: on a large volume a blanket recursive chown on
    # every boot is wasteful, and the common case is already correct.
    if [ "$(stat -c '%u' /data)" != "$(id -u appuser)" ]; then
        chown -R appuser:appuser /data
    fi
    exec gosu appuser "$@"
fi

exec "$@"
