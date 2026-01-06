# Widget Settings Migration

This migration adds support for configurable day ranges in dashboard chart widgets.

## For New Installations

The schema will be automatically created when you start the application. No action needed.

## For Existing Installations

Run the following command to apply the migration:

```bash
# Using Docker
docker exec -i daygle-mail-archiver-database psql -U daygle_mail_archiver -d daygle_mail_archiver < db/migration_add_widget_settings.sql

# Or directly with psql
psql -U daygle_mail_archiver -d daygle_mail_archiver -f db/migration_add_widget_settings.sql
```

## What This Adds

- New `user_widget_settings` table for storing widget configuration preferences
- Allows users to configure day ranges (7, 14, 30, 60, 90 days) for:
  - Emails Per Day chart
  - Email Deletions chart
  - Storage Trends chart

## Verifying the Migration

After running the migration, verify it was successful:

```sql
SELECT table_name FROM information_schema.tables 
WHERE table_schema = 'public' AND table_name = 'user_widget_settings';
```

You should see the `user_widget_settings` table listed.
