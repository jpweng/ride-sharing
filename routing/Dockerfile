# The routing image: OSRM serving car routes for Switzerland. Built by
# routing/build.sh, with the preprocessed data directory as the context; the
# OSRM image comes from there, pinned (data and server versions must match).
ARG OSRM_IMAGE
FROM ${OSRM_IMAGE}

ARG ALGORITHM=mld
ENV OSRM_ALGORITHM=${ALGORITHM}

COPY . /data/

EXPOSE 5000

# deploy/compose.yaml passes the production options (memory mapping, no
# turn-by-turn data); this default serves the data as is.
CMD ["sh", "-c", "exec osrm-routed --algorithm \"$OSRM_ALGORITHM\" --port 5000 /data/switzerland.osrm"]
