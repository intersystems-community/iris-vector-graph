# IRIS Password Reset Guide

**Problem**: `Password change required` error when connecting to IRIS database

**Error Message**:
```
RuntimeError: <COMMUNICATION LINK ERROR> Failed to connect to server
Details: <COMMUNICATION ERROR> Invalid Message received
Details: Password change required
```

## Solution

### Option 1: Disable Password Expiration (Recommended for Development)

**Source**: [`../rag-templates/archive/archived_documentation/migrations/IRIS_VERSION_MIGRATION_2025.md`](https://github.com/intersystems-community/rag-templates)

This is the cleanest solution for development/testing environments:

```bash
# Access IRIS terminal in %SYS namespace
docker exec -i iris-pgwire-db iris session iris -U%SYS <<'EOF'
do ##class(Security.Users).UnExpireUserPasswords("*")
write "Password expiration disabled for all users", !
quit
EOF
```

**Why this works**:
- Disables password expiration for all user accounts
- No need to change passwords or update .env files
- Persists across container restarts
- Standard InterSystems recommended approach for development

**For docker-compose automated setup**, add to your compose file:
```yaml
services:
  iris:
    command: --check-caps false -a "iris session iris -U%SYS '##class(Security.Users).UnExpireUserPasswords(\"*\")'"
```

### Option 2: Reset Password via Management Portal

1. **Access Management Portal**:
   ```bash
   # Standard IRIS
   open http://localhost:52773/csp/sys/UtilHome.csp

   # ACORN-1
   open http://localhost:252773/csp/sys/UtilHome.csp
   ```

2. **Login with current credentials**:
   - Username: `_SYSTEM`
   - Password: `SYS` (default) or your current password

3. **Change password when prompted**:
   - Enter new password (must meet complexity requirements)
   - Confirm new password

4. **Update .env file**:
   ```bash
   # Edit .env
   IRIS_PASSWORD=your_new_password
   ```

### Option 3: Reset Password via Docker Container

1. **Access IRIS terminal**:
   ```bash
   # Standard IRIS
   docker exec -it iris iris session iris

   # ACORN-1
   docker exec -it iris-acorn-1 iris session iris
   ```

2. **Change password in terminal**:
   ```objectscript
   // In IRIS terminal
   set sc = ##class(Security.Users).ChangePassword("_SYSTEM", "SYS", "NewPassword123")
   if sc write "Password changed successfully"
   ```

3. **Update .env file** (same as Option 1)

### Option 4: Recreate IRIS Container (Fresh Start)

If you don't need to preserve data:

```bash
# Stop and remove containers
docker-compose down -v  # -v removes volumes (DELETES DATA!)

# Start fresh
docker-compose up -d
# OR
docker-compose -f docker-compose.acorn.yml up -d

# Password will be reset to default: SYS
```

**⚠️ WARNING**: This deletes all data in the database!

### Option 5: Modify iris.key File (Advanced)

For persistent containers where you want to bypass password change:

1. **Access container**:
   ```bash
   docker exec -it iris bash
   ```

2. **Edit iris.key** (if accessible):
   ```bash
   # This method is container/deployment specific
   # Consult InterSystems documentation for your version
   ```

## Post-Reset Steps

1. **Update environment variables**:
   ```bash
   # .env file
   IRIS_PASSWORD=your_new_password

   # Or export for current session
   export IRIS_PASSWORD=your_new_password
   ```

2. **Test connection**:
   ```python
   import iris

   conn = iris.connect('localhost', 1972, 'USER', '_SYSTEM', 'your_new_password')
   print("Connection successful!")
   conn.close()
   ```

3. **Update docker-compose.yml** (if using environment variables):
   ```yaml
   services:
     iris:
       environment:
         - IRIS_PASSWORD=your_new_password
   ```

## Password Requirements

IRIS passwords typically must:
- Be at least 8 characters long
- Contain at least one uppercase letter
- Contain at least one lowercase letter
- Contain at least one number
- Not match common passwords

Example valid passwords:
- `MyPassword123`
- `IrisDb2024!`
- `SecurePass99`

## Common Issues

### Issue: "Password too simple"
**Solution**: Use a more complex password meeting all requirements above

### Issue: "Cannot access Management Portal"
**Solution**:
1. Check IRIS is running: `docker ps | grep iris`
2. Check correct port (52773 standard, 252773 ACORN-1)
3. Try localhost instead of 127.0.0.1 or vice versa

### Issue: "Password changed but still getting error"
**Solution**:
1. Restart IRIS container: `docker restart iris`
2. Clear connection cache: `rm -rf ~/.iris/cache/` (if exists)
3. Verify .env file is in correct location and loaded

## References

- [InterSystems IRIS Documentation - Security](https://docs.intersystems.com/irislatest/csp/docbook/DocBook.UI.Page.cls?KEY=GSA_config_users)
- [Docker Compose Documentation](https://docs.docker.com/compose/)
- rag-templates constitution: `IRIS_PASSWORD=SYS` for testing
- iris-embedded-python-template: CPF setup patterns

## See Also

- [`../rag-templates/CLAUDE.md`](../../../rag-templates/CLAUDE.md) - IRIS setup patterns
- [`../iris-pgwire/`](../../../iris-pgwire) - Recent IRIS connection patterns
- [`QUICKSTART.md`](./QUICKSTART.md) - Initial setup guide
