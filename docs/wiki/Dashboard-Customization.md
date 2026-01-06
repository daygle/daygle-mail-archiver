# Dashboard Customization

Learn how to customize your dashboard layout and widgets.

## Overview

The Daygle Mail Archiver dashboard is fully customizable with drag-and-drop widgets. Each user can personalize their dashboard layout.

## Customizing Your Dashboard

### Entering Edit Mode

1. Navigate to the **Dashboard**
2. Click the **Customize** button (gear icon) in the top right
3. The dashboard enters edit mode

### Rearranging Widgets

In edit mode, you can:

1. **Drag widgets** to new positions by clicking and dragging the widget header
2. **Resize widgets** by dragging the bottom-right corner
3. **Remove widgets** by clicking the X button in the top-right of each widget

### Adding Widgets

1. In edit mode, click the **Add Widget** button (+ icon)
2. Select a widget from the available list
3. The widget will be added to your dashboard
4. Drag it to your desired position

### Saving Your Layout

1. After making changes, click the **Save Layout** button
2. Your custom layout is saved to your user profile
3. Click **Cancel** to discard changes

### Resetting to Default

1. In edit mode, click the **Reset Layout** button
2. Confirm the reset
3. Your dashboard will revert to the default layout

## Available Widgets

### Overview
- **Total Emails**: Total emails archived
- **Emails Today**: Emails archived today
- **Database Size**: Current database size
- **Active Accounts**: Number of configured accounts
- **Workers Online**: Active worker processes

### Charts
- **Emails Per Day**: 30-day trend chart
- **Deletion Stats**: Manual vs. retention deletions
- **Top Senders**: Top 10 email senders
- **Top Receivers**: Top 10 email recipients
- **Storage Trends**: 7-day storage growth

### Health & Status
- **ClamAV Virus Scanning**: Virus scan statistics
  - Quarantined emails
  - Rejected emails
  - Logged only emails
  - Clean emails
- **Account Health**: Status of fetch accounts
- **Recent Activity**: Latest archived emails

## Widget Details

### Overview Widget

Shows key system metrics at a glance. Includes:
- System update notifications
- Quick statistics
- Worker status

### ClamAV Widget

Displays virus scanning statistics:
- **Quarantined**: Emails flagged as infected
- **Rejected**: Emails blocked from storage
- **Logged**: Emails stored despite detection
- **Clean**: Emails that passed scanning

See [ClamAV Virus Scanning](ClamAV-Virus-Scanning.md) for configuration.

### Account Health Widget

Shows status of all configured fetch accounts:
- **Healthy**: Account is working correctly
- **Error**: Account has errors (hover for details)
- **Never Fetched**: Account hasn't fetched yet
- **Disabled**: Account is disabled

### Charts

All charts are interactive:
- Hover over data points for details
- Charts update automatically
- Time ranges shown in chart titles

## Tips and Tricks

### Layout Ideas

**Monitoring Focus**:
- Large Overview widget at top
- Account Health and Worker Status below
- Charts at bottom

**Analytics Focus**:
- Charts across top half
- Stats widgets below
- Health monitoring at bottom

**Minimal Layout**:
- Overview widget
- Recent Activity
- One or two key charts

### Performance

- Removing unused widgets can improve page load time
- Charts update when you refresh the page
- Large layouts may take longer to load

## Next Steps

- [Configure email accounts](Email-Accounts-Setup.md)
- [Set up retention policies](Configuration.md)
- [Manage users](User-Management.md)
