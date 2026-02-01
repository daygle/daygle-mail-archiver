-- Migration: Add missing quarantine permissions
-- This migration adds restore_quarantine and delete_quarantine permissions
-- which were referenced in the code but missing from the database schema.

-- Add the missing permissions
INSERT INTO permissions (name, description, category) VALUES
    ('restore_quarantine', 'Restore quarantined emails to the archive', 'emails'),
    ('delete_quarantine', 'Permanently delete quarantined emails', 'emails')
ON CONFLICT (name) DO NOTHING;

-- Grant the new permissions to administrator role
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r, permissions p
WHERE r.name = 'administrator' AND p.name IN ('restore_quarantine', 'delete_quarantine')
ON CONFLICT DO NOTHING;

-- Grant the new permissions to email_manager role
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r, permissions p
WHERE r.name = 'email_manager' AND p.name IN ('restore_quarantine', 'delete_quarantine')
ON CONFLICT DO NOTHING;
