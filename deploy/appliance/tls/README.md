# Echo OS TLS material

Place one unencrypted PEM certificate chain at `echo.crt` and its matching
private key at `echo.key`. The key must have mode `0400` or `0600`; both files
must be regular files rather than symbolic links. Neither file is tracked by
Git.

The certificate must contain `ECHO_TLS_HOST` in its Subject Alternative Name
and remain valid for at least seven days. `start-tls.sh` verifies the format,
permissions, expiry, key pair and SAN before it asks Docker Compose to change
anything.
