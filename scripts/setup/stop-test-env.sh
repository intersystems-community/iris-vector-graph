#!/bin/bash
# =============================================================================
# Graph AI Test Environment Cleanup
# =============================================================================
# This script stops and cleans up the test environment
# =============================================================================

set -euo pipefail

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
COMPOSE_FILE="${PROJECT_ROOT}/docker-compose.test.yml"

# Function to print colored output
print_message() {
    local color=$1
    local message=$2
    echo -e "${color}[TEST-CLEANUP]${NC} ${message}"
}

# Function to stop test environment
stop_test_env() {
    print_message "$BLUE" "Stopping test environment..."

    cd "$PROJECT_ROOT"

    # Stop and remove containers
    docker-compose -f "$COMPOSE_FILE" down --remove-orphans

    print_message "$GREEN" "Test containers stopped"
}

# Function to clean up test data
cleanup_test_data() {
    if [[ "$1" == "--with-data" ]]; then
        print_message "$YELLOW" "Removing test data volumes..."

        # Remove volumes
        docker-compose -f "$COMPOSE_FILE" down -v

        print_message "$GREEN" "Test data volumes removed"
    fi
}

# Function to show usage
show_usage() {
    cat << EOF
Usage: $0 [OPTIONS]

Stop and clean up Graph AI test environment

OPTIONS:
    --with-data          Also remove test data volumes
    -h, --help           Show this help message

EXAMPLES:
    $0                   # Stop containers, keep data
    $0 --with-data       # Stop containers and remove data

EOF
}

# Parse command line arguments
WITH_DATA=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --with-data)
            WITH_DATA=true
            shift
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        *)
            print_message "$RED" "Unknown option: $1"
            show_usage
            exit 1
            ;;
    esac
done

# Main execution
main() {
    print_message "$BLUE" "Cleaning up Graph AI test environment..."

    # Execute cleanup steps
    stop_test_env

    if [[ "$WITH_DATA" == true ]]; then
        cleanup_test_data "--with-data"
    fi

    print_message "$GREEN" "Test environment cleanup completed!"
}

# Run main function
main "$@"