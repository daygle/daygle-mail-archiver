# Email Accounts Setup

Learn how to configure email accounts for archiving with IMAP, Gmail API, and Office 365 Graph API.

## Overview

Daygle Mail Archiver supports three methods for fetching emails:
1. **IMAP/IMAPS** - Standard protocol for most email providers
2. **Gmail API** - OAuth2-based access for Gmail accounts
3. **Office 365 Graph API** - OAuth2-based access for Microsoft 365 accounts

## IMAP Accounts

### Supported Providers

IMAP works with most email providers:
- Gmail (IMAP must be enabled)
- Outlook.com / Hotmail
- Office 365
- Yahoo Mail
- ProtonMail Bridge
- Custom mail servers

### Setup Steps

1. Navigate to **Fetch Accounts** in the web interface
2. Click **Create New Account**
3. Fill in the form:
   - **Account Type**: Select "IMAP"
   - **Email Address**: Your email address
   - **IMAP Server**: Server hostname (e.g., `imap.gmail.com`)
   - **IMAP Port**: Port number (usually 993 for SSL, 143 for STARTTLS)
   - **Username**: Usually your email address
   - **Password**: Your email password or app password
   - **Use SSL**: Enable for secure connections
   - **Folders**: Comma-separated list (e.g., `INBOX,Sent`)
4. Click **Test Connection** to verify
5. Click **Save** to create the account

### Common IMAP Settings

**Gmail**:
- Server: `imap.gmail.com`
- Port: 993
- SSL: Yes
- Note: Enable IMAP in Gmail settings and use an App Password

**Outlook.com / Office 365**:
- Server: `outlook.office365.com`
- Port: 993
- SSL: Yes

**Yahoo Mail**:
- Server: `imap.mail.yahoo.com`
- Port: 993
- SSL: Yes
- Note: Enable IMAP and use an App Password

### Advanced Options

- **Delete After Processing**: Automatically delete emails from server after archiving
- **Sync Interval**: How often to check for new emails (seconds)
- **Enabled**: Toggle to enable/disable the account

## Gmail API

Gmail API provides more reliable access to Gmail accounts using OAuth2.

### Prerequisites

1. A Google Cloud Project
2. Gmail API enabled
3. OAuth 2.0 credentials (Client ID and Client Secret)

### Google Cloud Console Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing one
3. Enable the Gmail API:
   - Navigate to **APIs & Services** → **Library**
   - Search for "Gmail API"
   - Click **Enable**
4. Create OAuth 2.0 credentials:
   - Navigate to **APIs & Services** → **Credentials**
   - Click **Create Credentials** → **OAuth client ID**
   - Application type: **Web application**
   - Add authorized redirect URI: `http://localhost:8000/oauth/gmail/callback`
   - Save the **Client ID** and **Client Secret**

### Setup in Mail Archiver

1. Navigate to **Fetch Accounts**
2. Click **Create New Account**
3. Fill in the form:
   - **Account Type**: Select "Gmail API"
   - **Email Address**: Gmail address to archive
   - **Client ID**: From Google Cloud Console
   - **Client Secret**: From Google Cloud Console
4. Click **Authorize** to complete OAuth flow
5. Click **Save**

### Features

- More reliable than IMAP
- Delta sync for efficient updates
- No password required (OAuth2)
- Access to all Gmail features

## Office 365 Graph API

Office 365 Graph API provides OAuth2 access to Microsoft 365 mailboxes.

### Prerequisites

1. Azure AD tenant with admin access
2. App registration in Azure AD
3. OAuth 2.0 credentials

### Azure AD Setup

1. Go to [Azure Portal](https://portal.azure.com/)
2. Navigate to **Azure Active Directory** → **App registrations**
3. Click **New registration**:
   - Name: "Daygle Mail Archiver"
   - Supported account types: Choose appropriate option
   - Redirect URI: `http://localhost:8000/oauth/office365/callback`
4. Note the **Application (client) ID**
5. Create a client secret:
   - Navigate to **Certificates & secrets**
   - Click **New client secret**
   - Save the **Value** (you won't see it again!)
6. Configure API permissions:
   - Navigate to **API permissions**
   - Add permission: **Microsoft Graph** → **Delegated permissions**
   - Add: `Mail.Read`, `Mail.ReadWrite` (if delete needed)
   - Click **Grant admin consent**

### Setup in Mail Archiver

1. Navigate to **Fetch Accounts**
2. Click **Create New Account**
3. Fill in the form:
   - **Account Type**: Select "Office 365 Graph API"
   - **Email Address**: Office 365 email address
   - **Client ID**: Application (client) ID from Azure
   - **Client Secret**: Secret value from Azure
   - **Tenant ID**: Your Azure AD tenant ID
4. Click **Authorize** to complete OAuth flow
5. Click **Save**

### Features

- OAuth2 authentication
- Delta sync support
- Works with shared mailboxes
- Modern API with better performance

## Testing Connections

Before saving, always click **Test Connection** to verify:
- Server connectivity
- Authentication
- Folder access

Common test errors and solutions are covered in [Troubleshooting](Troubleshooting.md).

## Best Practices

1. **Use App Passwords**: For IMAP with Gmail and Yahoo
2. **Enable SSL**: Always use SSL/TLS for security
3. **Limit Folders**: Only archive necessary folders
4. **Delta Sync**: Use Gmail API or Office 365 API for large mailboxes
5. **Monitor Logs**: Check worker logs for fetch errors

## Managing Accounts

### Editing Accounts

1. Navigate to **Fetch Accounts**
2. Click the **Edit** button for an account
3. Update settings
4. Click **Test Connection** to verify
5. Click **Save**

### Disabling Accounts

Temporarily disable without deleting:
1. Navigate to **Fetch Accounts**
2. Click the **Disable** button for an account

### Deleting Accounts

⚠️ **Warning**: Deleting an account does not delete archived emails.

1. Navigate to **Fetch Accounts**
2. Click the **Delete** button for an account
3. Confirm deletion

## Next Steps

- [Configure retention policies](Configuration.md#retention-policies)
- [Monitor worker status](Troubleshooting.md#worker-not-fetching-emails)
- [Set up virus scanning](ClamAV-Virus-Scanning.md)
