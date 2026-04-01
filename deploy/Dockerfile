FROM ivg-iris-base

ENV IRISUSERNAME "SuperUser"
ENV IRISPASSWORD "SYS"

WORKDIR /home/irisowner/dev

COPY ./projects/ObjectScript /tmp/cls
COPY ./projects/config.toml .
COPY ./projects/data /home/irisowner/data
COPY ./iris.script /opt/irisbuild/iris.script

RUN iris start IRIS \
    && iris session IRIS < /opt/irisbuild/iris.script \
    && iris stop IRIS quietly
