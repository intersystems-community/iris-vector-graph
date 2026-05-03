#!/bin/sh
# Start the webgateway in background
/startWebGateway &

# BUG 1 FIX: Wait for CSP.ini to be fully initialized before patching.
# /startWebGateway writes CSP.ini asynchronously — if you sed it immediately,
# the file gets regenerated and your changes are lost.
for i in $(seq 1 60); do
    grep -q "Configuration_Initialized" /opt/webgateway/bin/CSP.ini 2>/dev/null && break
    sleep 1
done

# BUG 2 FIX: Add credentials to [LOCAL] server section.
# Default tries CSPSystem which doesn't exist in fresh enterprise containers.
# Without Username/Password the webgateway gets 403 Access Denied from IRIS.
sed -i '/^\[LOCAL\]/a Username=_SYSTEM\nPassword=SYS' /opt/webgateway/bin/CSP.ini

# Point LOCAL at the IRIS container using the Docker service name (not localhost).
sed -i 's/^Ip_Address=127\.0\.0\.1/Ip_Address=iris/' /opt/webgateway/bin/CSP.ini

# BUG 3 FIX: Use CSP On directive (not SetHandler csp-handler-sa).
# SetHandler inside <Location> does not route through the CSP module correctly —
# Apache's filesystem handler intercepts first and returns 404.
# CSP On is the official ISC pattern from intersystems-community/webgateway-examples.
cat > /etc/apache2/conf-enabled/CSP.conf << "EOF"
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
EOF

apachectl graceful 2>/dev/null || true
wait
