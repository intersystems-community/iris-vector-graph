FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y \
    apache2 apache2-utils sudo curl \
    python3 python3-pip \
    build-essential pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -s /bin/bash irisowner

WORKDIR /tmp/iris_install

ARG VERSION=IRISHealth-2026.2.0AI.142.0-dockerubuntuarm64
COPY ${VERSION}.tar.gz .
COPY keys/iris.key /tmp/iris.key

RUN mkdir -p /tmp/extract && \
    tar -xzf ${VERSION}.tar.gz -C /tmp/extract && \
    cd /tmp/extract/${VERSION} && \
    ISC_PACKAGE_INSTANCENAME="IRIS" \
    ISC_PACKAGE_INSTALLDIR="/usr/irissys" \
    ISC_PACKAGE_PLATFORM="dockerubuntuarm64" \
    ISC_PACKAGE_INITIAL_SECURITY="Normal" \
    ISC_PACKAGE_USER_PASSWORD="SYS" \
    ISC_PACKAGE_CSPSYSTEM_PASSWORD="SYS" \
    ./irisinstall_silent && \
    mkdir -p /usr/irissys/mgr && \
    cp /tmp/iris.key /usr/irissys/mgr/iris.key && \
    cp /tmp/extract/${VERSION}/dist/dockerubuntuarm64/bin/iris-mcp-server /usr/irissys/bin/iris-mcp-server && \
    chmod +x /usr/irissys/bin/iris-mcp-server && \
    cd / && \
    rm -rf /tmp/iris_install /tmp/extract /tmp/iris.key

RUN /usr/irissys/bin/cspagent_install.sh --instancename IRIS --host 127.0.0.1 --port 1972 --apachebin /usr/sbin/apache2 --apachectl /usr/sbin/apachectl || true

RUN sed -i 's|#### END-ApacheCSP-SECTION ####|<Location "/mcp/">\n\tCSP On\n</Location>\n#### END-ApacheCSP-SECTION ####|' /etc/apache2/apache2.conf && \
    sed -i 's|/iris=Enabled|/iris=Enabled\n/mcp=Enabled|' /opt/webgateway/conf/CSP.ini && \
    printf '\n[APP_PATH:/mcp]\nDefault_Server=IRIS\nAlternative_Server_0=1~~~~~~IRIS\n' >> /opt/webgateway/conf/CSP.ini

RUN pip3 install --break-system-packages iris-vector-graph networkx lark numpy

RUN echo '#!/bin/bash' > /usr/irissys/iris-main.sh && \
    echo 'iris start IRIS' >> /usr/irissys/iris-main.sh && \
    echo 'apachectl start || true' >> /usr/irissys/iris-main.sh && \
    echo 'trap "iris stop IRIS quietly; apachectl stop" SIGTERM SIGINT' >> /usr/irissys/iris-main.sh && \
    echo 'tail -f /usr/irissys/mgr/messages.log &' >> /usr/irissys/iris-main.sh && \
    echo 'wait' >> /usr/irissys/iris-main.sh && \
    chmod +x /usr/irissys/iris-main.sh

ENV PATH="/usr/irissys/bin:$PATH"
WORKDIR /home/irisowner

EXPOSE 1972 52773 80 8888
ENTRYPOINT ["/usr/irissys/iris-main.sh"]
