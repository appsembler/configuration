# Build using: docker build -f Dockerfile.gocd-agent -t gocd-agent .
# https://hub.docker.com/r/gocd/gocd-agent-ubuntu-14.04/
FROM gocd/gocd-agent-ubuntu-14.04:v17.10.0

LABEL version="0.02" \
      description="This custom go-agent docker file installs additional requirements for the edx pipeline"

# Set locale to UTF-8 which is not the default for go-agent Docker container
RUN apt-get update &&\
    apt-get install -y locales &&\
    locale-gen en_US.UTF-8
ENV LANG en_US.UTF-8
ENV LANGUAGE en_US:en
ENV LC_ALL en_US.UTF-8

# starting with 16.04 apt-get-repository is not installed in the base image, lets install it so we are ready
# http://lifeonubuntu.com/ubuntu-missing-add-apt-repository-command/
RUN apt-get update && apt-get install -y -q \
    software-properties-common \
    python-software-properties

# Install a modern git client
RUN add-apt-repository -y ppa:git-core/ppa && \
    apt-get update && \
    apt-get -y install git

# Define working directory.
WORKDIR /data

# Define commonly used JAVA_HOME variable
ENV JAVA_HOME /usr/lib/jvm/java-7-oracle

# Install Python and package mgmt tools.
RUN apt-get update && apt-get install -y -q \
    python \
    python-dev \
    python-distribute \
    python-pip \
    libmysqlclient-dev

# Install php
RUN apt-get update && apt-get install -y \
    php5-common \
    php5-cli

# Install dependencies needed for Ansible 2.x
RUN apt-get update && apt-get install -y libffi-dev libssl-dev

# Install drush (drupal shell) for access to Drupal commands/Acquia
RUN php -r "readfile('http://files.drush.org/drush.phar');" > drush && \
    chmod +x drush && \
    sudo mv drush /usr/local/bin

# Install Docker - for Docker container building by a go-agent.
COPY docker/build/go-agent/files/docker_install.sh /tmp/docker/
RUN /bin/bash /tmp/docker/docker_install.sh

# Add the go user to the docker group to allow the go user to run docker commands.
# See: https://docs.docker.com/engine/installation/linux/ubuntulinux/
RUN usermod -aG docker go

# Assign the go user root privlidges
RUN printf "\ngo      ALL=(ALL:ALL) NOPASSWD: /usr/bin/pip, /usr/local/bin/pip\n" >> /etc/sudoers

# Upgrade pip and setup tools. Needed for Ansible 2.x
# Must upgrade to latest before pinning to work around bug
# https://github.com/pypa/pip/issues/3862
RUN \
  pip install --upgrade pip==9.0.3 && \
  pip3 install --upgrade pip==9.0.3 && \
  #pip may have moved from /usr/bin/ to /usr/local/bin/. This clears bash's path cache.
  hash -r && \
  pip install --upgrade pip==8.1.2 && \
  # upgrade setuptools early to avoid no distribution errors
  pip install --upgrade setuptools==39.0.1 && \
  pip3 install --upgrade setuptools==39.0.1


# Install AWS command-line interface - for AWS operations in a go-agent task.
RUN pip install awscli

ADD docker/build/go-agent/files/go-agent-start.sh /etc/service/go-agent/run
ADD docker/build/go-agent/files/go-agent-env-vars /etc/default/go-agent
RUN update-java-alternatives -s java-7-oracle

# !!!!NOTICE!!!! ---- Runner of this pipeline take heed!! You must replace go_github_key.pem with the REAL key material
# that can checkout private github repositories used as pipeline materials. The key material here is faked and is only
# used to pass CI!
# setup the github identity
ADD docker/build/go-agent/files/go_github_key.pem /home/go/.ssh/id_rsa
RUN chmod 600 /home/go/.ssh/id_rsa && \
    chown go:go /home/go/.ssh/id_rsa

# setup the known_hosts
RUN touch /home/go/.ssh/known_hosts && \
    chmod 600 /home/go/.ssh/known_hosts && \
    chown go:go /home/go/.ssh/known_hosts && \
    ssh-keyscan -t rsa,dsa github.com > /home/go/.ssh/known_hosts
