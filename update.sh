#!/usr/bin/env bash

# ============================================
# Daygle Mail Archiver - Update Script
# ============================================
# This script updates the Daygle Mail Archiver system by:
# - Fetching the latest code from the git repository
# - Pulling updated Docker images
# - Restarting the containers with the new code
#
# Usage:
#   ./update.sh                     # Interactive update
#   ./update.sh --check             # Check for updates without applying
#   ./update.sh --force             # Update without confirmation
#   ./update.sh --skip-start        # Don't start containers after update
#
# Based on mailcow update.sh design
# ============================================

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$SCRIPT_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
# Default values
FORCE=false
SKIP_START=false
CHECK_ONLY=false


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

# Function to show usage
show_usage() {
    echo -e "${CYAN}Daygle Mail Archiver - Update Script${NC}"
    echo "==============================================="
    echo ""
    cat <<EOF

${GREEN}Usage:${NC}
  $(basename "$0") [options]
EOF
    echo ""
    echo -e "${GREEN}Options:${NC}"
        cat <<EOF
    -c, --check           Check for updates without applying them
    -f, --force           Update without confirmation prompts
    --skip-start          Don't start containers after update
    -h, --help            Show this help message
EOF
    
    echo ""
    echo -e "${GREEN}Examples:${NC}"
    cat <<EOF
  # Check if updates are available
  ./update.sh --check

  # Interactive update (recommended)
  ./update.sh

  # Force update without prompts
  ./update.sh --force

  # Update but don't start containers (for manual inspection)
  ./update.sh --skip-start
EOF
    
    echo ""
    echo -e "${YELLOW}Important Notes:${NC}"
    cat <<EOF
    - Use --check first to see what will be updated
    - Your configuration file (daygle_mail_archiver.conf) is preserved

EOF
}

# Function to check if Docker Compose is available
check_docker_compose() {
    if command -v docker-compose &> /dev/null; then
        DOCKER_COMPOSE="docker-compose"
    elif docker compose version &> /dev/null; then
        DOCKER_COMPOSE="docker compose"
    else
        log_error "Docker Compose is not installed or not available"
        log_info "Please install Docker Compose: https://docs.docker.com/compose/install/"
        exit 1
    fi
    log_success "Using: $DOCKER_COMPOSE"
}

# Function to prune Docker build cache safely
prune_build_cache() {
    # Check if docker builder command is available (requires BuildKit)
    if docker builder version &> /dev/null; then
        log_info "Pruning Docker build cache..."
        docker builder prune -f 2>/dev/null || true
    else
        log_info "Docker BuildKit not available, skipping build cache pruning"
    fi
}

# Function to check if git is available
check_git() {
    if ! command -v git &> /dev/null; then
        log_error "Git is not installed"
        log_info "Please install Git to use the update script"
        exit 1
    fi
}

# Function to check if we're in a git repository
check_git_repo() {
    if ! git rev-parse --git-dir > /dev/null 2>&1; then
        log_error "Not a git repository"
        log_info "This script must be run from the Daygle Mail Archiver installation directory"
        exit 1
    fi
}

# Function to check for updates
check_for_updates() {
    log_info "Checking for updates..."
    
    # Get current branch
    BRANCH=$(git rev-parse --abbrev-ref HEAD)
    log_info "Current branch: $BRANCH"
    
    # Fetch latest changes
    if ! git fetch origin "$BRANCH" 2>&1; then
        log_error "Failed to fetch updates from remote repository"
        log_info "Please check your internet connection and git configuration"
        exit 1
    fi
    
    # Get current and latest commit
    CURRENT_COMMIT=$(git rev-parse HEAD)
    LATEST_COMMIT=$(git rev-parse "origin/$BRANCH")
    
    if [ "$CURRENT_COMMIT" == "$LATEST_COMMIT" ]; then
        log_success "You are already on the latest version!"
        echo ""
        log_info "Current commit: $(git log -1 --pretty=format:'%h - %s (%ar)' HEAD)"
        return 1
    else
        log_warning "Updates are available!"
        echo ""
        log_info "Your version:   $(git log -1 --pretty=format:'%h - %s (%ar)' HEAD)"
        log_info "Latest version: $(git log -1 --pretty=format:'%h - %s (%ar)' "origin/$BRANCH")"
        echo ""
        log_info "Changes:"
        echo ""
        git log --oneline --graph HEAD.."origin/$BRANCH"
        echo ""
        return 0
    fi
}

# (diff saving removed)

# Function to stop containers
stop_containers() {
    log_info "Stopping Daygle Mail Archiver containers..."
    cd "$ROOT_DIR"
    
    if ! $DOCKER_COMPOSE down; then
        log_error "Failed to stop containers"
        exit 1
    fi
    
    log_success "Containers stopped"
}

# Function to update code
update_code() {
    log_info "Updating code from git repository..."
    cd "$ROOT_DIR"
    
    # Get branch
    BRANCH=$(git rev-parse --abbrev-ref HEAD)
    
    # Configure git if needed (local to this repo only)
    [[ -z "$(git config user.name)" ]] && git config --local user.name "Daygle Mail Archiver"
    [[ -z "$(git config user.email)" ]] && git config --local user.email "update@daygle-mail-archiver"
    
    # Commit current state
    log_info "Saving current state..."
    git add -u
    git commit -am "Before update on ${TIMESTAMP}" > /dev/null 2>&1 || true
    
    # Fetch and merge
    log_info "Fetching latest changes..."
    git fetch origin "$BRANCH"
    
    log_info "Merging changes..."
    git config --local merge.defaultToUpstream true
    
    # Try to merge with strategy favoring upstream changes
    if ! git merge -X theirs -X patience -m "After update on ${TIMESTAMP}" "origin/$BRANCH"; then
        MERGE_EXIT=$?
        
        if [ $MERGE_EXIT -eq 128 ]; then
            log_error "Merge conflict detected!"
            log_error "You may have added files that conflict with the update"
            log_info "Please resolve conflicts manually or move conflicting files"
            exit 1
        else
            log_warning "Merge had issues, attempting to fix..."
            
            # Remove deleted upstream files
            deleted_files=$(git status --porcelain | grep -E "UD|DU" | awk '{print $2}')
            if [ -n "$deleted_files" ]; then
                echo "$deleted_files" | xargs rm -v 2>/dev/null || true
            fi
            git add -A
            git commit -m "After update on ${TIMESTAMP}" > /dev/null 2>&1 || true
            git checkout . 2>/dev/null || true
            
            log_success "Conflicts resolved"
        fi
    fi
    
    log_success "Code updated successfully"
}

# Function to pull Docker images
pull_images() {
    log_info "Pulling latest Docker images..."
    cd "$ROOT_DIR"
    
    if ! $DOCKER_COMPOSE pull; then
        log_error "Failed to pull Docker images"
        log_info "You may need to check your internet connection"
        exit 1
    fi
    
    log_success "Docker images updated"
}

# Function to start containers
start_containers() {
    if [ "$SKIP_START" = true ]; then
        log_warning "Skipping container startup (--skip-start flag set)"
        echo ""
        log_info "To start the containers manually, run:"
        log_info "  cd $ROOT_DIR"
        log_info "  $DOCKER_COMPOSE up -d --build --remove-orphans"
        return 0
    fi
    
    log_info "Starting Daygle Mail Archiver..."
    cd "$ROOT_DIR"
    
    # First attempt: try building normally
    if ! $DOCKER_COMPOSE up -d --build --remove-orphans; then
        log_warning "Build failed, possibly due to corrupted Docker cache. Attempting to rebuild without cache..."
        
        # Prune build cache to fix corrupted cache layers
        prune_build_cache
        
        # Retry with --no-cache to avoid cache issues
        if ! $DOCKER_COMPOSE build --no-cache; then
            log_error "Failed to build containers even without cache"
            log_error "This may indicate a configuration or dependency issue"
            log_info "Check the build logs above and ensure all required files are present"
            log_info "You can also check logs with: $DOCKER_COMPOSE logs -f"
            exit 1
        fi
        
        # Start the containers after successful build
        if ! $DOCKER_COMPOSE up -d --remove-orphans; then
            log_error "Failed to start containers"
            log_info "Check the logs with: $DOCKER_COMPOSE logs -f"
            exit 1
        fi
    fi
    
    log_success "Containers started successfully"
}

# Function to apply database schema migrations
apply_schema() {
    if [ "$SKIP_START" = true ]; then
        log_info "Skipping database schema update (--skip-start flag set)"
        log_info "To apply schema updates manually, run:"
        log_info "  $DOCKER_COMPOSE exec db psql -U ${POSTGRES_USER:-daygle_mail_archiver} -d ${POSTGRES_DB:-daygle_mail_archiver} -f /docker-entrypoint-initdb.d/schema.sql"
        return
    fi

    log_info "Applying database schema updates..."
    cd "$ROOT_DIR"

    local pg_user="${POSTGRES_USER:-daygle_mail_archiver}"
    local pg_db="${POSTGRES_DB:-daygle_mail_archiver}"

    # Wait for the database to be ready (it should already be healthy after start_containers,
    # but we poll for up to 30 seconds to handle any edge-case timing)
    local attempts=0
    until $DOCKER_COMPOSE exec -T db pg_isready -U "$pg_user" -d "$pg_db" > /dev/null 2>&1; do
        if [ $attempts -ge 15 ]; then
            log_warning "Database did not become ready in time - skipping schema update"
            log_info "You can apply schema updates manually with:"
            log_info "  $DOCKER_COMPOSE exec db psql -U $pg_user -d $pg_db -f /docker-entrypoint-initdb.d/schema.sql"
            return
        fi
        sleep 2
        attempts=$((attempts + 1))
    done

    # schema.sql is written entirely with idempotent SQL (CREATE TABLE IF NOT EXISTS,
    # ALTER TABLE ... ADD COLUMN IF NOT EXISTS, INSERT ... ON CONFLICT DO NOTHING),
    # so it is safe to re-run against an existing database.
    # Suppress harmless NOTICE messages that appear when objects already exist.
    local schema_output
    if schema_output=$($DOCKER_COMPOSE exec -T db \
            env PGOPTIONS="-c client_min_messages=WARNING" \
            psql -U "$pg_user" -d "$pg_db" -q \
            -f /docker-entrypoint-initdb.d/schema.sql 2>&1); then
        log_success "Database schema updated"
    else
        log_warning "Schema update completed with errors:"
        echo "$schema_output" | grep -iv "^notice:" || true
        log_info "You can apply schema updates manually with:"
        log_info "  $DOCKER_COMPOSE exec db psql -U $pg_user -d $pg_db -f /docker-entrypoint-initdb.d/schema.sql"
    fi
}

# Function to clean up old Docker resources
cleanup_docker() {
    log_info "Cleaning up old Docker resources..."
    
    # Remove dangling images
    dangling_images=$(docker images -f "dangling=true" -q)
    if [ -n "$dangling_images" ]; then
        echo "$dangling_images" | xargs docker rmi 2>/dev/null || true
    fi
    
    # Prune build cache to prevent cache corruption issues
    prune_build_cache
    
    log_success "Cleanup completed"
}

# Function to show post-update information
show_post_update_info() {
    echo ""
    echo "============================================================"
    echo ""
    log_success "Daygle Mail Archiver has been updated successfully!"
    echo ""
    echo "============================================================"
    echo ""
    
    log_info "Access the web interface at: http://localhost:8000"
    echo ""
    
    if [ "$SKIP_START" = false ]; then
        log_info "Checking container status..."
        cd "$ROOT_DIR"
        $DOCKER_COMPOSE ps
    fi
    
    echo ""
    log_info "View logs with: $DOCKER_COMPOSE logs -f"
    log_info "Stop services with: $DOCKER_COMPOSE down"
    echo ""
    
    # Local diffs disabled; nothing to show
}

# Parse command line arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -c|--check)
                CHECK_ONLY=true
                shift
                ;;
            -f|--force)
                FORCE=true
                log_warning "Running in force mode - will not prompt for confirmation"
                shift
                ;;
            --skip-start)
                SKIP_START=true
                shift
                ;;
            
            -h|--help)
                show_usage
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                echo ""
                show_usage
                exit 1
                ;;
        esac
    done
}

# Main update function
main() {
    echo ""
    echo "============================================================"
    echo ""
    echo -e "        ${CYAN}Daygle Mail Archiver - Update Script${NC}"
    echo ""
    echo "============================================================"
    echo ""
    
    # Parse arguments
    parse_args "$@"
    
    # Pre-flight checks
    log_info "Running pre-flight checks..."
    check_git
    check_git_repo
    check_docker_compose
    echo ""
    
    # Check if we're in the right directory
    if [ ! -f "$ROOT_DIR/docker-compose.yml" ]; then
        log_error "docker-compose.yml not found"
        log_info "Please run this script from the Daygle Mail Archiver installation directory"
        exit 1
    fi
    
    # Check for updates
    if check_for_updates; then
        # Updates available
        if [ "$CHECK_ONLY" = true ]; then
            log_info "Use './update.sh' to apply these updates"
            exit 0
        fi
        
        # Confirm update
        if [ "$FORCE" = false ]; then
            echo ""
            read -p "Do you want to update now? (y/n): " -r
            echo ""
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                log_info "Update cancelled"
                exit 0
            fi
        fi
        
        # (local diff saving removed) proceed with update
        
        # Perform update
        stop_containers
        update_code
        pull_images
        start_containers
        apply_schema
        cleanup_docker
        
        # Show success message
        show_post_update_info
        
    else
        # No updates available
        if [ "$CHECK_ONLY" = false ]; then
            log_info "Nothing to update"
        fi
        exit 0
    fi
}

# Run main function
main "$@"
