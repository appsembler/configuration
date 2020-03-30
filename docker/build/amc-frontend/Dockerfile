FROM node:10

RUN apt-get update && apt-get -y --no-install-recommends install \
    ca-certificates \
    curl

RUN which yarn  # Make sure yarn is installed

RUN mkdir -p /opt/amc/frontend
WORKDIR /opt/amc/frontend
