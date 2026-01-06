# Security Notes

Important security considerations for Daygle Mail Archiver.

## Critical Security Tasks

### Change Default Credentials

⚠️ **CRITICAL**: Always change default security values in production!

#### Database Password

Edit `daygle_mail_archiver.conf`:

```ini
[database]
password = your_strong_password_here
```

Also update in `docker-compose.yml`:

```yaml
services:
  db:
    environment:
      - POSTGRES_PASSWORD=your_strong_password_here
```

#### Session Secret

Generate a new session secret:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Update in `daygle_mail_archiver.conf`:

```ini
[security]
session_secret = your_generated_secret_here
```

#### IMAP Password Encryption Key

Generate a new Fernet key:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Update in `daygle_mail_archiver.conf`:

```ini
[security]
imap_password_key = your_generated_key_here
```

⚠️ **WARNING**: Changing this key will invalidate all stored IMAP passwords!

## Best Practices

### Network Security

#### Firewall Configuration

Only expose necessary ports:

```bash
# Allow API access (web interface)
sudo ufw allow 8000/tcp

# Block direct database access from outside
# (only accessible within Docker network)
```

#### HTTPS/SSL

For production, use a reverse proxy with SSL:

**Nginx Example**:

```nginx
server {
    listen 443 ssl;
    server_name mail-archive.example.com;
    
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    
    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

**Caddy Example**:

```
mail-archive.example.com {
    reverse_proxy localhost:8000
}
```

### User Security

#### Strong Passwords

Enforce strong passwords:
- Minimum 8 characters
- Uppercase and lowercase letters
- Numbers required
- Consider special characters

#### Role-Based Access

- Use **Read Only** role for users who only need to view emails
- Grant **Administrator** role sparingly
- Regularly audit user accounts

#### Account Management

- Disable unused accounts promptly
- Remove accounts for departed users
- Regularly review user list

### Data Security

#### Email Data Sensitivity

Email archives contain sensitive information:
- Personal communications
- Business data
- Potentially confidential attachments

Protect accordingly:
- Restrict system access
- Use strong authentication
- Enable virus scanning
- Regular backups to secure location

#### Backup Security

- **Encrypt backups** if storing off-site
- **Restrict access** to backup files
- **Use secure transfer** methods (SCP, SFTP, not FTP)
- **Store in secure location** with access controls

#### Database Security

- Database runs inside Docker network (not exposed externally)
- Change default password
- Regularly update PostgreSQL image
- Monitor for security updates

### OAuth Security

#### Gmail API

- Keep Client Secret secure
- Don't commit to version control
- Regularly rotate credentials
- Limit OAuth scopes to minimum required

#### Office 365 API

- Keep Client Secret secure
- Set expiration dates for secrets
- Monitor Azure AD sign-in logs
- Use admin consent for organization-wide deployment

### Container Security

#### Keep Images Updated

Regularly update Docker images:

```bash
docker compose pull
docker compose up -d
```

#### Limit Container Resources

Prevent resource exhaustion:

```yaml
services:
  api:
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 512M
```

#### Run as Non-Root

Containers should not run as root user. The project images are configured appropriately.

### Application Security

#### Security Headers

Add security headers via reverse proxy:

```nginx
add_header X-Frame-Options "SAMEORIGIN" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-XSS-Protection "1; mode=block" always;
add_header Referrer-Policy "no-referrer-when-downgrade" always;
add_header Content-Security-Policy "default-src 'self' http: https: data: blob: 'unsafe-inline'" always;
```

#### Session Security

- Sessions expire after inactivity
- Session secrets must be changed from defaults
- Sessions stored server-side only

### Monitoring & Auditing

#### Audit Logs

- All user actions are logged
- Review logs regularly for suspicious activity
- Monitor failed login attempts

Access audit logs:
1. Navigate to **Logs** in web interface
2. Filter by level and source
3. Export if needed

#### System Logs

Monitor Docker logs regularly:

```bash
docker compose logs -f
```

Watch for:
- Failed authentication attempts
- Unusual database queries
- Connection errors
- Virus detections

### Virus Scanning Security

See [ClamAV Virus Scanning](ClamAV-Virus-Scanning.md) for:
- Enabling virus scanning
- Configuring actions for infected emails
- Managing quarantined emails

### Compliance Considerations

#### Data Retention

- Configure retention policies appropriately
- Document retention periods
- Ensure compliance with regulations (GDPR, HIPAA, etc.)

#### Data Access

- Log all data access
- Implement need-to-know access
- Regular access reviews

#### Data Deletion

- Proper deletion procedures
- Consider "right to be forgotten" requirements
- Secure deletion from backups if required

## Security Checklist

Before going to production:

- [ ] Changed database password
- [ ] Changed session secret
- [ ] Changed IMAP password encryption key
- [ ] Configured HTTPS/SSL via reverse proxy
- [ ] Firewall configured to limit access
- [ ] Strong passwords enforced for users
- [ ] Virus scanning enabled
- [ ] Backups configured and encrypted
- [ ] Audit logging enabled
- [ ] Regular update schedule planned
- [ ] User roles properly assigned
- [ ] OAuth credentials secured

## Reporting Security Issues

If you discover a security vulnerability:

1. **Do NOT** open a public GitHub issue
2. Email security concerns to the maintainers
3. Include detailed description and reproduction steps
4. Allow time for patch development before disclosure

## Next Steps

- [Configure virus scanning](ClamAV-Virus-Scanning.md)
- [Set up backups](Backup-and-Restore.md)
- [Manage users](User-Management.md)
