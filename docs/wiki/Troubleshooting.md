# Troubleshooting

Common issues and solutions for Daygle Mail Archiver.

## Common Issues

### Worker Not Fetching Emails

**Symptoms**: Emails are not being archived from configured accounts.

**Solutions**:

1. **Check worker status**:
   ```bash
   docker compose ps worker
   docker compose logs worker
   ```

2. **Verify account is enabled**:
   - Navigate to **Fetch Accounts**
   - Ensure account is enabled (green status)

3. **Test connection**:
   - Edit the fetch account
   - Click **Test Connection**
   - Fix any connection errors

4. **Check credentials**:
   - IMAP: Verify username/password
   - Gmail/O365: Re-authorize OAuth

5. **Restart worker**:
   ```bash
   docker compose restart worker
   ```

### Cannot Login

**Symptoms**: Unable to login with correct credentials.

**Solutions**:

1. **Check if account is disabled**:
   - Contact administrator to check account status

2. **Reset password** (administrator only):
   - Access database directly
   - Or recreate user account

3. **Check API logs**:
   ```bash
   docker compose logs api
   ```

4. **Clear browser cache** and try again

5. **Try different browser** to rule out browser issues

### Database Connection Issues

**Symptoms**: "Database connection error" messages.

**Solutions**:

1. **Check database container**:
   ```bash
   docker compose ps db
   docker compose logs db
   ```

2. **Verify configuration**:
   - Check `daygle_mail_archiver.conf` settings
   - Ensure database credentials are correct

3. **Restart database**:
   ```bash
   docker compose restart db
   ```

4. **Check disk space**:
   ```bash
   df -h
   ```

5. **Check database health**:
   ```bash
   docker compose exec db psql -U daygle_mail_archiver -c "SELECT 1"
   ```

### OAuth Authorization Fails

**Symptoms**: Gmail or Office 365 OAuth authorization fails.

**Solutions for Gmail**:

1. **Verify redirect URI** in Google Cloud Console:
   - Must be: `http://localhost:8000/oauth/gmail/callback`
   - Or match your actual domain

2. **Check API is enabled**:
   - Gmail API must be enabled in Google Cloud Console

3. **Verify credentials**:
   - Client ID and Client Secret must match

4. **Check scope permissions**:
   - Ensure Gmail API has required scopes

**Solutions for Office 365**:

1. **Verify redirect URI** in Azure AD:
   - Must match actual callback URL

2. **Check API permissions**:
   - `Mail.Read` permission required
   - Admin consent granted

3. **Verify tenant ID** is correct

4. **Check client secret** hasn't expired

### Emails Not Being Deleted from Mail Server

**Symptoms**: Emails remain on server after archiving with "delete after processing" enabled.

**Solutions**:

1. **Verify account setting**:
   - Check "Delete After Processing" is enabled
   - Some providers don't support deletion

2. **Check IMAP permissions**:
   - Account must have delete permissions
   - Some providers require special settings

3. **For Gmail**: 
   - Emails are moved to trash, not deleted immediately
   - Empty trash to permanently delete

4. **Check worker logs** for errors:
   ```bash
   docker compose logs worker | grep -i delete
   ```

### ClamAV Issues

See [ClamAV Virus Scanning](ClamAV-Virus-Scanning.md#troubleshooting) for virus scanning specific issues.

### Container Won't Start

**Symptoms**: Container fails to start or immediately exits.

**Solutions**:

1. **Check logs**:
   ```bash
   docker compose logs [service-name]
   ```

2. **Verify configuration file** syntax:
   - Check for typos in `daygle_mail_archiver.conf`

3. **Check port conflicts**:
   ```bash
   # Check if port 8000 is in use
   sudo netstat -tulpn | grep 8000
   ```

4. **Check disk space**:
   ```bash
   df -h
   ```

5. **Rebuild containers**:
   ```bash
   docker compose down
   docker compose up -d --build
   ```

### High Memory Usage

**Symptoms**: System running out of memory.

**Solutions**:

1. **Check container resource usage**:
   ```bash
   docker stats
   ```

2. **ClamAV memory usage**:
   - ClamAV uses 1-2 GB RAM
   - Disable if not needed
   - Increase system RAM

3. **Database memory**:
   - PostgreSQL memory grows with data
   - Add more RAM
   - Consider dedicated database server

4. **Limit container resources** in `docker-compose.yml`:
   ```yaml
   deploy:
     resources:
       limits:
         memory: 512M
   ```

### Slow Performance

**Symptoms**: Web interface is slow or emails fetch slowly.

**Solutions**:

1. **Check system resources**:
   ```bash
   docker stats
   htop
   ```

2. **Optimize database**:
   ```bash
   docker compose exec db psql -U daygle_mail_archiver daygle_mail_archiver -c "VACUUM ANALYZE"
   ```

3. **Use SSD storage** for database

4. **Reduce fetch frequency** for accounts

5. **Limit archived folders** to essential ones

### Update Failures

**Symptoms**: Update script fails or containers won't restart.

**Solutions**:

1. **Check update logs**:
   ```bash
   ./update.sh 2>&1 | tee update.log
   ```

2. **Manual update**:
   ```bash
   git pull
   docker compose down
   docker compose up -d --build
   ```

3. **Restore from backup** if needed

4. **Check Docker disk space**:
   ```bash
   docker system df
   docker system prune
   ```

## Getting Help

If issues persist:

1. **Check logs** for all services:
   ```bash
   docker compose logs --tail=100
   ```

2. **Review GitHub Issues**: [Issues Page](https://github.com/daygle/daygle-mail-archiver/issues)

3. **Create new issue** with:
   - Problem description
   - Steps to reproduce
   - Log output
   - System information

## Useful Commands

### View All Logs
```bash
docker compose logs -f
```

### Check Container Health
```bash
docker compose ps
```

### Restart All Services
```bash
docker compose restart
```

### Clean Docker Resources
```bash
docker system prune -a
```

### Database Console Access
```bash
docker compose exec db psql -U daygle_mail_archiver daygle_mail_archiver
```

## Next Steps

- [Check configuration](Configuration.md)
- [Review security notes](Security-Notes.md)
- [Contact support via GitHub Issues](https://github.com/daygle/daygle-mail-archiver/issues)
