# Backup and Restore

Learn how to backup and restore your Daygle Mail Archiver data.

## Overview

Daygle Mail Archiver includes built-in backup and restore functionality for:
- PostgreSQL database (emails, settings, users)
- Configuration file
- Optional: log files

## Backup Methods

### Web Interface (Coming Soon)

The web interface will provide a user-friendly backup and restore feature.

### Command-Line Backup

Use the included backup script for manual backups.

## Creating Backups

### Manual Backup

```bash
# Navigate to installation directory
cd /opt/daygle-mail-archiver

# Create backup
docker compose exec -T db pg_dump -U daygle_mail_archiver daygle_mail_archiver | gzip > backup_$(date +%Y%m%d_%H%M%S).sql.gz

# Backup configuration
cp daygle_mail_archiver.conf backup_daygle_mail_archiver.conf
```

### Automated Backup Script

Create a backup script:

```bash
#!/bin/bash
BACKUP_DIR="/backups/daygle"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR

# Backup database
docker compose exec -T db pg_dump -U daygle_mail_archiver daygle_mail_archiver | gzip > $BACKUP_DIR/db_$DATE.sql.gz

# Backup config
cp daygle_mail_archiver.conf $BACKUP_DIR/config_$DATE.conf

# Keep only last 30 days
find $BACKUP_DIR -name "*.sql.gz" -mtime +30 -delete
find $BACKUP_DIR -name "*.conf" -mtime +30 -delete
```

Save as `backup.sh`, make executable, and add to cron:

```bash
chmod +x backup.sh
crontab -e
# Add: 0 2 * * * /opt/daygle-mail-archiver/backup.sh
```

## Restoring from Backup

### Database Restore

⚠️ **Warning**: Restoring will replace ALL current data!

```bash
# Stop services
docker compose stop api worker

# Restore database
gunzip < backup_20240115_020000.sql.gz | docker compose exec -T db psql -U daygle_mail_archiver daygle_mail_archiver

# Restart services
docker compose start api worker
```

### Configuration Restore

```bash
# Restore configuration file
cp backup_daygle_mail_archiver.conf daygle_mail_archiver.conf

# Restart services
docker compose restart
```

## Best Practices

### Backup Frequency
- **Production**: Daily automated backups
- **Development**: Weekly or before major changes
- **Before Updates**: Always backup before updating

### Backup Storage
- Store backups on separate storage/server
- Use cloud storage for off-site backups
- Test restore procedures regularly
- Keep multiple backup versions (30+ days)

### Security
- Encrypt backups if storing off-site
- Restrict access to backup files
- Backups contain sensitive email data
- Use secure transfer methods (SCP, SFTP)

## Backup to Cloud Storage

### AWS S3 Example

```bash
#!/bin/bash
BACKUP_FILE="backup_$(date +%Y%m%d_%H%M%S).sql.gz"

# Create backup
docker compose exec -T db pg_dump -U daygle_mail_archiver daygle_mail_archiver | gzip > $BACKUP_FILE

# Upload to S3
aws s3 cp $BACKUP_FILE s3://my-bucket/daygle-backups/

# Clean local file
rm $BACKUP_FILE
```

### Other Cloud Providers

Similar commands work with:
- **Google Cloud Storage**: `gsutil cp`
- **Azure Blob Storage**: `az storage blob upload`
- **Backblaze B2**: `b2 upload-file`

## Disaster Recovery

### Full System Recovery

1. **Install fresh system** following [Installation Guide](Installation-Guide.md)
2. **Restore configuration**:
   ```bash
   cp backup_daygle_mail_archiver.conf daygle_mail_archiver.conf
   ```
3. **Start containers**:
   ```bash
   docker compose up -d
   ```
4. **Wait for database initialization**
5. **Restore database**:
   ```bash
   docker compose stop api worker
   gunzip < backup.sql.gz | docker compose exec -T db psql -U daygle_mail_archiver daygle_mail_archiver
   docker compose start api worker
   ```

### Partial Recovery

To restore only specific data, use SQL:

```bash
# Extract specific table
docker compose exec -T db pg_dump -U daygle_mail_archiver -t emails daygle_mail_archiver | gzip > emails_backup.sql.gz

# Restore specific table
gunzip < emails_backup.sql.gz | docker compose exec -T db psql -U daygle_mail_archiver daygle_mail_archiver
```

## Verification

After restore, verify:

1. **Login works**: Access web interface
2. **Emails visible**: Check email list
3. **Search works**: Test search functionality
4. **Accounts configured**: Verify fetch accounts
5. **Settings correct**: Check global settings

## Next Steps

- [Configure automated backups](#automated-backup-script)
- [Test restore procedure](#restoring-from-backup)
- [Set up cloud storage](#backup-to-cloud-storage)
