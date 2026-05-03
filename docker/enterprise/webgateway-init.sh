#!/bin/sh
/startWebGateway &
WG_PID=$!

for i in $(seq 1 60); do
    if grep -q "Configuration_Initialized" /opt/webgateway/bin/CSP.ini 2>/dev/null; then
        break
    fi
    sleep 1
done

cat > /opt/webgateway/bin/CSP.ini << 'CSPEOF'
[SYSTEM]
IRISCONNECT_LIBRARY_PATH=/opt/webgateway/bin
System_Manager=*.*.*.*
SM_Timeout=28800
Server_Response_Timeout=60
No_Activity_Timeout=86400
Queued_Request_Timeout=60
Event_Log_Rotation_Size=2000000000

[SYSTEM_INDEX]
LOCAL=Enabled

[LOCAL]
Username=_SYSTEM
Password=]]]U1lT
Ip_Address=iris
TCP_Port=1972
Minimum_Server_Connections=3
Maximum_Session_Connections=6

[APP_PATH_INDEX]
/=Enabled
/api=Enabled
/isc=Enabled
/csp=Enabled

[APP_PATH:/]
Default_Server=LOCAL
Alternative_Server_0=1~~~~~~LOCAL

[APP_PATH:/csp]
Default_Server=LOCAL
Alternative_Server_0=1~~~~~~LOCAL

[APP_PATH:/api]
Default_Server=LOCAL
Alternative_Server_0=1~~~~~~LOCAL

[APP_PATH:/isc]
Default_Server=LOCAL
Alternative_Server_0=1~~~~~~LOCAL
CSPEOF

cat > /etc/apache2/conf-enabled/CSP.conf << 'APACHEEOF'
CSPModulePath "${ISC_PACKAGE_INSTALLDIR}/bin/"
CSPConfigPath "${ISC_PACKAGE_INSTALLDIR}/bin/"

<Location />
    CSP On
</Location>

<Directory "${ISC_PACKAGE_INSTALLDIR}/bin/">
    AllowOverride None
    Options None
    Require all granted
    <FilesMatch "\.(log|ini|pid|exe)$">
         Require all denied
    </FilesMatch>
</Directory>
APACHEEOF

apachectl graceful 2>/dev/null || true

wait $WG_PID
