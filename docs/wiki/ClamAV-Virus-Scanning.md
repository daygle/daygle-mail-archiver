# ClamAV Virus Scanning

Configure and manage virus scanning for incoming emails using ClamAV.

## Overview

Daygle Mail Archiver includes integrated ClamAV virus scanning to protect against malware and viruses in email attachments. All incoming emails can be automatically scanned before storage.

## Features

- **Automatic scanning** of all incoming emails
- **Configurable actions** for infected emails
- **Real-time virus definition updates**
- **Dashboard statistics** showing scan results
- **Quarantine management** for infected emails

## Configuration

### Enabling Virus Scanning

1. Navigate to **Global Settings** in the web interface
2. Scroll to the **ClamAV Virus Scanning** section
3. Enable **Enable Virus Scanning** checkbox
4. Configure ClamAV connection:
   - **ClamAV Host**: `clamav` (default for Docker)
   - **ClamAV Port**: `3310` (default)

### Action When Virus Detected

Choose how to handle infected emails:

#### Quarantine (Recommended)
- Emails with viruses are stored in the database
- Marked with a virus detection flag
- Can be reviewed and managed by administrators
- Allows for false positive review

#### Reject
- Emails with viruses are NOT stored
- Rejected immediately after detection
- Most secure option
- No recovery possible if false positive

#### Log Only
- All emails are stored regardless of virus status
- Virus detections are logged
- Useful for monitoring and testing
- Least secure option

## ClamAV Container

### Container Status

Check ClamAV container status:

```bash
docker compose ps clamav
```

View ClamAV logs:

```bash
docker compose logs clamav
```

### Virus Definition Updates

ClamAV automatically updates virus definitions daily. To manually update:

```bash
docker compose exec clamav freshclam
```

### Resource Requirements

ClamAV requires:
- **Memory**: 1-2 GB RAM minimum
- **Disk Space**: ~500 MB for virus definitions
- **Startup Time**: 5-10 minutes on first run

## Dashboard Statistics

The **ClamAV Virus Scanning** widget on the dashboard shows:

- **Quarantined**: Emails stored with virus flag
- **Rejected**: Emails blocked from storage
- **Logged Only**: Emails stored despite virus detection
- **Clean**: Emails scanned and passed

## Viewing Infected Emails

### Viewing Quarantined Emails

1. Navigate to **Emails**
2. Check the virus scan column (shield icon)
3. Red badge indicates infected email
4. Click on email to view details
5. Virus name and scan timestamp shown

### Email Details

Infected emails show:
- **Virus Name**: Identified threat name
- **Scan Timestamp**: When the scan occurred
- **Virus Detected Badge**: Visual indicator

⚠️ **Warning**: Do not download or open infected emails unless you know what you're doing!

## Troubleshooting

### ClamAV Not Starting

If ClamAV container fails to start:

1. **Check logs**:
   ```bash
   docker compose logs clamav
   ```

2. **Insufficient memory**:
   - ClamAV requires at least 1 GB RAM
   - Increase Docker memory limit
   - Edit `docker-compose.yml` to add resource limits

3. **Virus definition download failure**:
   - Check internet connectivity
   - Virus definitions download on first startup
   - Wait 5-10 minutes for completion

### Emails Not Being Scanned

If emails are not being scanned:

1. **Verify ClamAV is enabled** in Global Settings
2. **Check ClamAV container is healthy**:
   ```bash
   docker compose ps
   ```
3. **Check worker logs** for connection errors:
   ```bash
   docker compose logs worker
   ```
4. **Verify ClamAV port** is correct (default: 3310)

### Disable Virus Scanning

To temporarily disable scanning:

1. Navigate to **Global Settings**
2. Uncheck **Enable Virus Scanning**
3. Click **Save Settings**

To completely remove ClamAV:

1. Edit `docker-compose.yml`
2. Comment out or remove the `clamav` service
3. Restart containers:
   ```bash
   docker compose down
   docker compose up -d
   ```

### High False Positive Rate

If you're getting too many false positives:

1. **Update virus definitions**:
   ```bash
   docker compose exec clamav freshclam
   ```
2. **Switch to "Log Only" mode** to monitor detections
3. **Review quarantined emails** to identify patterns
4. **Report false positives** to ClamAV project

## Performance Considerations

### Impact on Email Ingestion

- Scanning adds ~1-5 seconds per email
- Large attachments take longer to scan
- Performance depends on:
  - Email size
  - Number of attachments
  - Available system resources

### Optimizing Performance

1. **Increase memory** allocated to ClamAV container
2. **Use SSD storage** for better I/O performance
3. **Monitor resource usage**:
   ```bash
   docker stats clamav
   ```

## Security Best Practices

1. **Enable virus scanning** for all production systems
2. **Use "Quarantine" mode** to allow review
3. **Regularly update** virus definitions (automatic)
4. **Monitor dashboard** for infection trends
5. **Review quarantined emails** periodically
6. **Keep Docker images updated** for latest ClamAV version

## Next Steps

- [View dashboard statistics](Dashboard-Customization.md)
- [Configure retention policies](Configuration.md)
- [Monitor system logs](Troubleshooting.md)
