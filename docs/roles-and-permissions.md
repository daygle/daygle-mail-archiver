# Roles and permissions

## Access model

Users receive the combined permissions from every role assigned to them. Authorization is enforced server-side; a missing navigation link is not a security boundary.

The default permission categories are:

- **General**: dashboard and personal profile access
- **Emails**: viewing, importing, exporting, deleting, and quarantine operations
- **Reports**: operational, security, and data-quality reporting
- **System**: fetch accounts, worker status, logs, and alerts
- **Administration**: users, roles, and global settings

The exact permission list is stored in the `permissions` table and is rendered dynamically by Role Management.

## Role Management page

Users with `manage_roles` can open **Role Management**. The page provides:

- Responsive cards for every role
- Built-in/custom role labels
- Permission counts and assigned-user counts
- Search across role names, descriptions, and permission names
- A filter for built-in roles
- A create-role modal with permission groups, permission search, and select-all controls

Built-in system roles are protected and cannot be edited or deleted. Custom roles can be edited at any time; deletion is blocked while users are assigned to the role.

## Creating and editing roles

1. Open **Role Management** and select **Create role**.
2. Enter a clear role name and an optional description.
3. Select only the permissions required for the job. Review each category before submitting.
4. Save the role, then assign it to users from **User Management**.

Role names are normalized into a unique slug. Removing permissions is allowed and is the preferred way to reduce access. Updating a role replaces its permission set atomically, so a failed update does not leave a partial assignment.

## Privilege escalation guardrails

The application protects privileged permissions (`manage_users`, `manage_roles`, and `manage_global_settings`). An actor may not grant a privileged permission to a role unless the actor already holds that permission. The same rule applies when assigning roles to users.

Administrators should review role changes in the Logs page and avoid giving `manage_roles` more broadly than necessary.

## Useful permissions

| Permission | Purpose |
| --- | --- |
| `view_emails` | Browse archived email records |
| `import_emails` / `export_emails` | Import or export email data |
| `view_quarantine` | View quarantined messages |
| `restore_quarantine` | Restore quarantined messages |
| `delete_quarantine` | Permanently remove quarantine records, including supported server cleanup modes |
| `view_reports` / `view_security_reports` | View general or security reports |
| `view_logs` | View audit and operational logs |
| `manage_alerts` | Configure alert triggers and severity |
| `manage_fetch_accounts` | Create and manage provider accounts |
| `manage_users` | Create, edit, and disable users |
| `manage_roles` | Create and manage custom roles |
| `manage_global_settings` | Change system-wide settings |

Permission checks are also applied directly to API routes, so manually posting to a route cannot bypass the role model.
