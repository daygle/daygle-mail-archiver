#!/usr/bin/env bash

# ============================================
# Daygle Mail Archiver - Backup & Restore
# ============================================
# This script provides backup and restore functionality for the entire
# Daygle Mail Archiver system, including both the PostgreSQL database
# and environment configuration (.env file with encryption keys).
#
# Usage:
#   ./helper-scripts/backup_and_restore.sh backup
#   ./helper-scripts/backup_and_restore.sh restore <backup_file.tar.gz>
#   ./helper-scripts/backup_and_restore.sh list
#   ./helper-scripts/backup_and_restore.sh delete <backup_file.tar.gz>
#
# Backups are stored in ./backups/ directory by default.
# ============================================

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="${ROOT_DIR}/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Function to print colored messages
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check if Docker Compose is available
check_docker_compose() {
    if command -v docker-compose &> /dev/null; then
        DOCKER_COMPOSE="docker-compose"
    elif docker compose version &> /dev/null; then
        DOCKER_COMPOSE="docker compose"
    else
        log_error "Docker Compose is not installed or not available"
        exit 1
    fi
}

# Function to check if containers are running
check_containers() {
    cd "$ROOT_DIR"
    if ! $DOCKER_COMPOSE ps | grep -q "daygle-mail-archiver-database"; then
        log_error "Daygle Mail Archiver containers are not running"
        log_info "Please start the system with: $DOCKER_COMPOSE up -d"
        exit 1
    fi
}

# Function to load .env file
load_env() {
    if [ ! -f "$ROOT_DIR/.env" ]; then
        log_error ".env file not found at $ROOT_DIR/.env"
        log_info "Please create .env file from .env.example"
        exit 1
    fi
    
    # Load environment variables
    set -a
    source "$ROOT_DIR/.env"
    set +a
}

# Function to create backup
backup() {
    log_info "Starting backup process..."
    
    # Create backup directory if it doesn't exist
    mkdir -p "$BACKUP_DIR"
    
    # Create temporary directory for this backup
    TEMP_BACKUP_DIR=$(mktemp -d)
    trap "rm -rf $TEMP_BACKUP_DIR" EXIT
    
    log_info "Creating database backup..."
    
    # Dump PostgreSQL database
    cd "$ROOT_DIR"
    $DOCKER_COMPOSE exec -T db pg_dump -U "$DB_USER" -d "$DB_NAME" > "$TEMP_BACKUP_DIR/database.sql"
    
    if [ $? -eq 0 ]; then
        log_success "Database backup created"
    else
        log_error "Database backup failed"
        exit 1
    fi
    
    # Copy .env file
    log_info "Backing up environment configuration..."
    if [ -f "$ROOT_DIR/.env" ]; then
        cp "$ROOT_DIR/.env" "$TEMP_BACKUP_DIR/.env"
        log_success "Environment configuration backed up"
    else
        log_warning ".env file not found, skipping"
    fi
    
    # Create metadata file
    cat > "$TEMP_BACKUP_DIR/backup_metadata.txt" <<EOF
Daygle Mail Archiver Backup
============================
Backup Date: $(date)
Backup Timestamp: $TIMESTAMP
Database: $DB_NAME
Database User: $DB_USER

Contents:
- database.sql: Full PostgreSQL database dump
- .env: Environment configuration with encryption keys

IMPORTANT: Keep this backup secure as it contains:
- IMAP_PASSWORD_KEY: Required to decrypt IMAP account passwords
- SESSION_SECRET: Required for session cookies
- Database credentials

To restore this backup:
  ./helper-scripts/backup_and_restore.sh restore daygle_backup_${TIMESTAMP}.tar.gz
EOF
    
    log_info "Creating compressed backup archive..."
    
    # Create tar.gz archive
    BACKUP_FILE="$BACKUP_DIR/daygle_backup_${TIMESTAMP}.tar.gz"
    tar -czf "$BACKUP_FILE" -C "$TEMP_BACKUP_DIR" .
    
    if [ $? -eq 0 ]; then
        BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
        log_success "Backup completed successfully!"
        log_info "Backup file: $BACKUP_FILE"
        log_info "Backup size: $BACKUP_SIZE"
        echo ""
        log_warning "IMPORTANT: Store this backup securely. It contains:"
        echo "  - Complete database with all emails"
        echo "  - Encryption keys (IMAP_PASSWORD_KEY, SESSION_SECRET)"
        echo "  - Database credentials"
    else
        log_error "Failed to create backup archive"
        exit 1
    fi
}

# Function to restore from backup
restore() {
    local backup_file="$1"
    
    # Check if backup file exists
    if [ ! -f "$backup_file" ]; then
        # Try in backups directory
        if [ -f "$BACKUP_DIR/$backup_file" ]; then
            backup_file="$BACKUP_DIR/$backup_file"
        else
            log_error "Backup file not found: $backup_file"
            exit 1
        fi
    fi
    
    log_warning "WARNING: This will overwrite your current database and .env file!"
    log_warning "All existing data will be replaced with the backup."
    echo ""
    read -p "Are you sure you want to continue? (yes/no): " -r
    echo
    
    if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
        log_info "Restore cancelled"
        exit 0
    fi
    
    log_info "Starting restore process..."
    
    # Create temporary directory for extraction
    TEMP_RESTORE_DIR=$(mktemp -d)
    trap "rm -rf $TEMP_RESTORE_DIR" EXIT
    
    # Extract backup
    log_info "Extracting backup archive..."
    tar -xzf "$backup_file" -C "$TEMP_RESTORE_DIR"
    
    if [ $? -ne 0 ]; then
        log_error "Failed to extract backup archive"
        exit 1
    fi
    
    # Validate backup contents
    if [ ! -f "$TEMP_RESTORE_DIR/database.sql" ]; then
        log_error "Invalid backup: database.sql not found"
        exit 1
    fi
    
    # Restore .env file
    if [ -f "$TEMP_RESTORE_DIR/.env" ]; then
        log_info "Restoring environment configuration..."
        
        # Backup current .env
        if [ -f "$ROOT_DIR/.env" ]; then
            cp "$ROOT_DIR/.env" "$ROOT_DIR/.env.backup_$(date +%Y%m%d_%H%M%S)"
            log_info "Current .env backed up to .env.backup_*"
        fi
        
        cp "$TEMP_RESTORE_DIR/.env" "$ROOT_DIR/.env"
        log_success "Environment configuration restored"
        
        # Reload environment variables
        load_env
    else
        log_warning ".env file not found in backup"
    fi
    
    # Restore database
    log_info "Restoring database..."
    log_warning "This may take several minutes for large databases..."
    
    cd "$ROOT_DIR"
    
    # Drop existing database connections and recreate
    # Use printf to properly escape the database name and avoid SQL injection
    $DOCKER_COMPOSE exec -T db psql -U "$DB_USER" -d postgres -v db_name="$DB_NAME" <<'EOF'
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :'db_name' AND pid <> pg_backend_pid();
EOF
    
    # Drop and recreate database using psql commands with proper escaping
    $DOCKER_COMPOSE exec -T db psql -U "$DB_USER" -d postgres -c "DROP DATABASE IF EXISTS \"$DB_NAME\""
    
    if [ $? -ne 0 ]; then
        log_error "Failed to drop existing database"
        exit 1
    fi
    
    $DOCKER_COMPOSE exec -T db psql -U "$DB_USER" -d postgres -c "CREATE DATABASE \"$DB_NAME\" OWNER \"$DB_USER\""
    
    if [ $? -ne 0 ]; then
        log_error "Failed to create database"
        exit 1
    fi
    
    # Restore database dump
    $DOCKER_COMPOSE exec -T db psql -U "$DB_USER" -d "$DB_NAME" < "$TEMP_RESTORE_DIR/database.sql"
    
    if [ $? -eq 0 ]; then
        log_success "Database restored successfully"
    else
        log_error "Database restore failed"
        exit 1
    fi
    
    log_success "Restore completed successfully!"
    log_info "System restored from backup: $(basename "$backup_file")"
    echo ""
    log_warning "IMPORTANT: Restart the services to apply changes:"
    echo "  cd $ROOT_DIR"
    echo "  $DOCKER_COMPOSE restart"
}

# Function to list available backups
list_backups() {
    log_info "Available backups in $BACKUP_DIR:"
    echo ""
    
    if [ ! -d "$BACKUP_DIR" ] || [ -z "$(ls -A "$BACKUP_DIR" 2>/dev/null)" ]; then
        log_warning "No backups found"
        exit 0
    fi
    
    # List backup files with details
    for backup in "$BACKUP_DIR"/daygle_backup_*.tar.gz; do
        if [ -f "$backup" ]; then
            size=$(du -h "$backup" | cut -f1)
            date=$(stat -c %y "$backup" 2>/dev/null || stat -f %Sm "$backup" 2>/dev/null)
            echo -e "${GREEN}$(basename "$backup")${NC}"
            echo "  Size: $size"
            echo "  Created: $date"
            echo ""
        fi
    done
}

# Function to delete a backup
delete_backup() {
    local backup_file="$1"
    
    # Check if backup file exists
    if [ ! -f "$backup_file" ]; then
        # Try in backups directory
        if [ -f "$BACKUP_DIR/$backup_file" ]; then
            backup_file="$BACKUP_DIR/$backup_file"
        else
            log_error "Backup file not found: $backup_file"
            exit 1
        fi
    fi
    
    log_warning "This will permanently delete the backup file:"
    log_info "$(basename "$backup_file")"
    echo ""
    read -p "Are you sure you want to delete this backup? (yes/no): " -r
    echo
    
    if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
        log_info "Delete cancelled"
        exit 0
    fi
    
    rm -f "$backup_file"
    
    if [ $? -eq 0 ]; then
        log_success "Backup deleted: $(basename "$backup_file")"
    else
        log_error "Failed to delete backup"
        exit 1
    fi
}

# Function to show usage
show_usage() {
    cat <<EOF
Daygle Mail Archiver - Backup & Restore Script
===============================================

Usage:
  $(basename "$0") <command> [options]

Commands:
  backup                    Create a new backup of database and .env file
  restore <backup_file>     Restore from a backup file
  list                      List all available backups
  delete <backup_file>      Delete a specific backup file

Examples:
  # Create a new backup
  ./helper-scripts/backup_and_restore.sh backup

  # List available backups
  ./helper-scripts/backup_and_restore.sh list

  # Restore from a backup
  ./helper-scripts/backup_and_restore.sh restore daygle_backup_20240105_120000.tar.gz
  ./helper-scripts/backup_and_restore.sh restore backups/daygle_backup_20240105_120000.tar.gz

  # Delete a backup
  ./helper-scripts/backup_and_restore.sh delete daygle_backup_20240105_120000.tar.gz

Notes:
  - Backups are stored in ./backups/ directory
  - Backups include both database dump and .env file with encryption keys
  - Always test restores on a non-production system first
  - Keep backups secure - they contain sensitive encryption keys

EOF
}

# Main script logic
main() {
    if [ $# -eq 0 ]; then
        show_usage
        exit 0
    fi
    
    local command="$1"
    
    case "$command" in
        backup)
            check_docker_compose
            check_containers
            load_env
            backup
            ;;
        restore)
            if [ $# -lt 2 ]; then
                log_error "Missing backup file argument"
                echo "Usage: $0 restore <backup_file.tar.gz>"
                exit 1
            fi
            check_docker_compose
            check_containers
            load_env
            restore "$2"
            ;;
        list)
            list_backups
            ;;
        delete)
            if [ $# -lt 2 ]; then
                log_error "Missing backup file argument"
                echo "Usage: $0 delete <backup_file.tar.gz>"
                exit 1
            fi
            delete_backup "$2"
            ;;
        help|--help|-h)
            show_usage
            ;;
        *)
            log_error "Unknown command: $command"
            echo ""
            show_usage
            exit 1
            ;;
    esac
}

# Run main function
main "$@"
