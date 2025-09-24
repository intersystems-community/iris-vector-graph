#!/bin/bash
# =============================================================================
# Graph AI Test Runner
# =============================================================================
# This script runs tests with automatic Docker environment management
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
TEST_ENV_SETUP="${SCRIPT_DIR}/setup-test-env.sh"
TEST_ENV_TEARDOWN="${SCRIPT_DIR}/stop-test-env.sh"

# Default options
AUTO_SETUP=true
AUTO_TEARDOWN=true
KEEP_ENV=false
TEST_PATTERN=""
VERBOSE=false

# Function to print colored output
print_message() {
    local color=$1
    local message=$2
    echo -e "${color}[TEST-RUNNER]${NC} ${message}"
}

# Function to check if test environment is running
check_test_env() {
    if docker-compose -f "${PROJECT_ROOT}/docker-compose.test.yml" ps iris_test | grep -q "Up (healthy)" 2>/dev/null; then
        return 0
    else
        return 1
    fi
}

# Function to setup test environment
setup_test_env() {
    if [[ "$AUTO_SETUP" == true ]]; then
        if check_test_env; then
            print_message "$GREEN" "Test environment already running"
        else
            print_message "$BLUE" "Setting up test environment..."
            "$TEST_ENV_SETUP"
        fi
    fi
}

# Function to teardown test environment
teardown_test_env() {
    if [[ "$AUTO_TEARDOWN" == true && "$KEEP_ENV" != true ]]; then
        print_message "$BLUE" "Tearing down test environment..."
        "$TEST_ENV_TEARDOWN"
    fi
}

# Function to run tests
run_tests() {
    print_message "$BLUE" "Running tests..."

    cd "$PROJECT_ROOT"

    # Set test environment
    export NODE_ENV=test
    export DOTENV_CONFIG_PATH=.env.test

    # Build Jest command
    local jest_cmd="npx jest"
    local jest_args=()

    if [[ -n "$TEST_PATTERN" ]]; then
        jest_args+=("--testPathPattern=$TEST_PATTERN")
    fi

    if [[ "$VERBOSE" == true ]]; then
        jest_args+=("--verbose")
    fi

    # Add any additional arguments passed to this script
    jest_args+=("$@")

    # Run tests with proper environment
    print_message "$BLUE" "Executing: $jest_cmd ${jest_args[*]}"

    if $jest_cmd "${jest_args[@]}"; then
        print_message "$GREEN" "All tests passed!"
        return 0
    else
        print_message "$RED" "Some tests failed!"
        return 1
    fi
}

# Function to show test environment status
show_env_status() {
    print_message "$BLUE" "Test Environment Status:"

    if check_test_env; then
        echo -e "  ${GREEN}IRIS Test DB:${NC}    Running (port 1973)"
        echo -e "  ${GREEN}Management:${NC}      http://localhost:52774/csp/sys/UtilHome.csp"
    else
        echo -e "  ${RED}IRIS Test DB:${NC}    Not running"
    fi

    echo -e "  ${BLUE}Environment:${NC}     test"
    echo -e "  ${BLUE}Config:${NC}          .env.test"
}

# Function to show usage
show_usage() {
    cat << EOF
Usage: $0 [OPTIONS] [JEST_ARGS...]

Run Graph AI tests with automatic Docker environment management

OPTIONS:
    -p, --pattern PATTERN    Test pattern to match
    -v, --verbose           Verbose test output
    -k, --keep-env          Keep test environment running after tests
    -s, --skip-setup        Skip automatic test environment setup
    -t, --skip-teardown     Skip automatic test environment teardown
    --status                Show test environment status and exit
    -h, --help              Show this help message

EXAMPLES:
    $0                                      # Run all tests
    $0 --pattern integration                # Run integration tests only
    $0 --pattern database-performance       # Run performance tests only
    $0 --verbose --keep-env                 # Verbose output, keep environment
    $0 --skip-setup --skip-teardown        # Use existing environment
    $0 --coverage                           # Jest coverage (passed through)

JEST ARGUMENTS:
    Any additional arguments are passed directly to Jest

EOF
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -p|--pattern)
            TEST_PATTERN="$2"
            shift 2
            ;;
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        -k|--keep-env)
            KEEP_ENV=true
            shift
            ;;
        -s|--skip-setup)
            AUTO_SETUP=false
            shift
            ;;
        -t|--skip-teardown)
            AUTO_TEARDOWN=false
            shift
            ;;
        --status)
            show_env_status
            exit 0
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        *)
            # Pass remaining arguments to Jest
            break
            ;;
    esac
done

# Main execution
main() {
    print_message "$BLUE" "Starting Graph AI test execution..."

    # Trap to ensure cleanup on exit
    trap 'teardown_test_env' EXIT

    # Setup test environment
    setup_test_env

    # Show environment status
    show_env_status
    echo

    # Run tests
    if run_tests "$@"; then
        print_message "$GREEN" "Test execution completed successfully!"
        exit 0
    else
        print_message "$RED" "Test execution failed!"
        exit 1
    fi
}

# Error handling
trap 'print_message "$RED" "Script failed on line $LINENO"' ERR

# Run main function with all arguments
main "$@"