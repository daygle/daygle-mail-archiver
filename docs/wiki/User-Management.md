# User Management

Learn how to manage users and configure role-based access control.

## User Roles

Daygle Mail Archiver supports two user roles:

### Administrator
- Full access to all features
- Can create, edit, and delete users
- Can modify global settings
- Can delete emails
- Can access all configuration options

### Read Only
- View archived emails only
- Search and filter emails
- Download emails in EML format
- Cannot modify settings or delete emails
- Limited to viewing permissions

## Creating Users

### Initial Administrator

The first administrator account is created during initial setup through the setup wizard.

### Creating Additional Users

1. Log in as an administrator
2. Navigate to **User Management**
3. Click **Create New User**
4. Fill in the form:
   - **Username**: Unique username (required)
   - **First Name**: User's first name (optional)
   - **Last Name**: User's last name (optional)
   - **Email**: Email address for notifications (optional)
   - **Password**: Strong password (required)
   - **Role**: Select Administrator or Read Only
   - **Account Status**: Enabled or Disabled
5. Click **Create User**

### Password Requirements

Passwords must meet the following requirements:
- At least 8 characters long
- Contains uppercase and lowercase letters
- Contains at least one number

## Managing Existing Users

### Editing Users

1. Navigate to **User Management**
2. Click the **Edit** button next to a user
3. Update user information
4. Leave password blank to keep current password
5. Click **Save Changes**

### Disabling Users

Temporarily disable a user account without deleting:

1. Navigate to **User Management**
2. Click the **Disable** button next to a user
3. The user will no longer be able to log in

Re-enable by clicking the **Enable** button.

### Deleting Users

⚠️ **Warning**: Deleting a user is permanent and cannot be undone.

1. Navigate to **User Management**
2. Click the **Delete** button next to a user
3. Confirm deletion

**Note**: You cannot delete your own account.

## User Profile Management

Users can manage their own profiles:

1. Click on your username in the top navigation
2. Select **Profile**
3. Update personal information:
   - First Name, Last Name
   - Email address
   - Date/time formats
   - Timezone preferences
4. Change password if needed
5. Click **Save Changes**

### Changing Password

Users can change their own password:

1. Navigate to **Profile**
2. Enter current password
3. Enter new password
4. Confirm new password
5. Click **Change Password**

## Best Practices

### Security
- Use strong, unique passwords for each user
- Regularly review user accounts and disable unused ones
- Use the Read Only role for users who only need to view emails
- Enable two-factor authentication if available in future versions

### Organization
- Use meaningful usernames (e.g., `john.doe` instead of `user1`)
- Fill in first name, last name, and email for easy identification
- Disable accounts instead of deleting when users leave temporarily

### Access Control
- Grant Administrator role only to trusted users
- Regularly audit user permissions
- Remove accounts for departed users promptly

## Audit Logging

All user actions are logged in the audit log:
- User logins
- User creation, modification, deletion
- Email deletions
- Configuration changes

View audit logs by navigating to **Logs** in the sidebar.

## Next Steps

- [Customize your dashboard](Dashboard-Customization.md)
- [Configure global settings](Configuration.md)
- [View audit logs](Troubleshooting.md)
