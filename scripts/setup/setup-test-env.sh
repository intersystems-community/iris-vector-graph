#!/bin/bash
# =============================================================================
# Graph AI Test Environment Setup
# =============================================================================
# This script sets up the test environment with IRIS Community Edition
# =============================================================================

set -euo pipefail

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
COMPOSE_FILE="${PROJECT_ROOT}/docker-compose.test.yml"
ENV_FILE="${PROJECT_ROOT}/.env.test"

# Function to print colored output
print_message() {
    local color=$1
    local message=$2
    echo -e "${color}[TEST-SETUP]${NC} ${message}"
}

# Function to check prerequisites
check_prerequisites() {
    print_message "$BLUE" "Checking prerequisites..."

    # Check Docker
    if ! command -v docker &> /dev/null; then
        print_message "$RED" "ERROR: Docker is not installed"
        exit 1
    fi

    # Check Docker Compose
    if ! command -v docker-compose &> /dev/null; then
        print_message "$RED" "ERROR: Docker Compose is not installed"
        exit 1
    fi

    # Check if Docker daemon is running
    if ! docker info &> /dev/null; then
        print_message "$RED" "ERROR: Docker daemon is not running"
        exit 1
    fi

    print_message "$GREEN" "Prerequisites check passed"
}

# Function to start test database
start_test_db() {
    print_message "$BLUE" "Starting IRIS test database..."

    cd "$PROJECT_ROOT"

    # Stop any existing test containers
    docker-compose -f "$COMPOSE_FILE" down --remove-orphans 2>/dev/null || true

    # Start test database
    docker-compose -f "$COMPOSE_FILE" up -d

    print_message "$BLUE" "Waiting for IRIS test database to be healthy..."

    local max_wait=120  # 2 minutes
    local wait_time=0
    local check_interval=5

    while [[ $wait_time -lt $max_wait ]]; do
        if docker-compose -f "$COMPOSE_FILE" ps iris_test | grep -q "Up (healthy)" 2>/dev/null; then
            print_message "$GREEN" "IRIS test database is ready!"
            return 0
        fi

        print_message "$YELLOW" "Waiting for database... (${wait_time}s/${max_wait}s)"
        sleep $check_interval
        wait_time=$((wait_time + check_interval))
    done

    print_message "$RED" "Timeout waiting for database to become healthy"
    docker-compose -f "$COMPOSE_FILE" logs iris_test
    return 1
}

# Function to initialize test schema
init_test_schema() {
    print_message "$BLUE" "Initializing test database schema..."

    # Load the actual schema.sql file
    if [[ -f "$PROJECT_ROOT/sql/schema.sql" ]]; then
        print_message "$BLUE" "Loading schema.sql..."
        docker-compose -f "$COMPOSE_FILE" exec -T iris_test iris sql -U USER < "$PROJECT_ROOT/sql/schema.sql"

        if [[ $? -eq 0 ]]; then
            print_message "$GREEN" "Schema loaded successfully"
        else
            print_message "$RED" "Failed to load schema.sql"
            return 1
        fi
    else
        print_message "$RED" "schema.sql not found at $PROJECT_ROOT/sql/schema.sql"
        return 1
    fi
}

# Function to load stored procedures
load_procedures() {
    print_message "$BLUE" "Loading stored procedures..."

    # Load operators.sql which contains our custom procedures (kg_KNN_VEC, kg_RRF_FUSE, etc.)
    if [[ -f "$PROJECT_ROOT/sql/operators.sql" ]]; then
        print_message "$BLUE" "Loading operators.sql..."
        docker-compose -f "$COMPOSE_FILE" exec -T iris_test iris sql -U USER < "$PROJECT_ROOT/sql/operators.sql"

        if [[ $? -eq 0 ]]; then
            print_message "$GREEN" "Stored procedures loaded successfully"
        else
            print_message "$RED" "Failed to load operators.sql"
            return 1
        fi
    else
        print_message "$RED" "operators.sql not found at $PROJECT_ROOT/sql/operators.sql"
        return 1
    fi
}

# Function to load test data
load_test_data() {
    print_message "$BLUE" "Loading sample data with embeddings..."

    # Load sample data with 768D vectors
    if [[ -f "$PROJECT_ROOT/scripts/sample_data_768.sql" ]]; then
        print_message "$BLUE" "Loading sample_data_768.sql..."
        docker-compose -f "$COMPOSE_FILE" exec -T iris_test iris sql -U USER < "$PROJECT_ROOT/scripts/sample_data_768.sql"

        if [[ $? -eq 0 ]]; then
            print_message "$GREEN" "Sample data with embeddings loaded successfully"
        else
            print_message "$RED" "Failed to load sample_data_768.sql"
            return 1
        fi
    else
        print_message "$YELLOW" "Warning: sample_data_768.sql not found, skipping..."
    fi
}

# Function to configure ODBC for tests
configure_odbc() {
    print_message "$BLUE" "Configuring ODBC for tests..."

    # Check if ODBC DSN exists, create if needed
    if ! grep -q "IRIS_TEST" ~/.odbc.ini 2>/dev/null; then
        print_message "$BLUE" "Creating ODBC DSN for test database..."

        cat >> ~/.odbc.ini << EOF

[IRIS_TEST]
Driver=/usr/local/lib/libirisodbcur35.so
Host=localhost
Port=1973
Database=USER
UID=_SYSTEM
PWD=SYS
Description=IRIS Test Database for Graph AI
EOF
        print_message "$GREEN" "ODBC DSN created"
    else
        print_message "$GREEN" "ODBC DSN already exists"
    fi
}

# Function to verify test environment
verify_test_env() {
    print_message "$BLUE" "Verifying test environment..."

    # Test database connection
    local test_result=$(docker-compose -f "$COMPOSE_FILE" exec -T iris_test iris session iris -U%SYS << 'EOF' 2>/dev/null | tail -n 1 || echo "ERROR"
set stmt = ##class(%SQL.Statement).%New()
set result = stmt.%ExecDirect("SELECT 1 as test")
if result.%Next() {
    write "CONNECTION_OK"
} else {
    write "CONNECTION_FAILED"
}
halt
EOF
)

    if echo "$test_result" | grep -q "CONNECTION_OK"; then
        print_message "$GREEN" "Database connection verified"
    else
        print_message "$RED" "Database connection failed"
        return 1
    fi

    # Test native IRIS vector functions
    local vector_test=$(docker-compose -f "$COMPOSE_FILE" exec -T iris_test iris sql -U USER << 'EOF' 2>/dev/null | tail -n 1 || echo "ERROR"
SELECT VECTOR_COSINE(TO_VECTOR('[1,0,0]'), TO_VECTOR('[1,0,0]')) as cosine_similarity;
EOF
)

    if echo "$vector_test" | grep -q "1"; then
        print_message "$GREEN" "Native IRIS vector functions verified (VECTOR_COSINE, TO_VECTOR)"
    else
        print_message "$RED" "Native IRIS vector functions failed"
        print_message "$YELLOW" "Vector test output: $vector_test"
        return 1
    fi

    # Test custom stored procedures
    local procedure_test=$(docker-compose -f "$COMPOSE_FILE" exec -T iris_test iris sql -U USER << 'EOF' 2>/dev/null || echo "ERROR"
CALL kg_KNN_VEC('[0.1,0.2,0.9]', 1, 'Gene');
EOF
    )

    if echo "$procedure_test" | grep -q "HGNC"; then
        print_message "$GREEN" "Custom stored procedures verified (kg_KNN_VEC working)"
    else
        print_message "$YELLOW" "Custom procedures test result: $procedure_test"
        print_message "$YELLOW" "Procedures may need sample data to return results"
    fi

    print_message "$GREEN" "Test environment verification completed"
}

# Function to show status
show_status() {
    print_message "$GREEN" "Test environment is ready!"
    echo
    print_message "$BLUE" "Test Database Info:"
    echo -e "  ${GREEN}Host:${NC}           localhost"
    echo -e "  ${GREEN}Port:${NC}           1973"
    echo -e "  ${GREEN}Management:${NC}     http://localhost:52774/csp/sys/UtilHome.csp"
    echo -e "  ${GREEN}DSN:${NC}            IRIS_TEST"
    echo -e "  ${GREEN}User:${NC}           _SYSTEM"
    echo -e "  ${GREEN}Password:${NC}       SYS"
    echo
    print_message "$BLUE" "Run tests with:"
    echo -e "  ${YELLOW}npm test${NC}                    # Run all tests"
    echo -e "  ${YELLOW}npm run test:integration${NC}    # Integration tests only"
    echo -e "  ${YELLOW}npm run test:e2e${NC}           # E2E tests only"
    echo -e "  ${YELLOW}npm run test:performance${NC}    # Performance tests with timing"
    echo
    print_message "$BLUE" "Stop test environment with:"
    echo -e "  ${YELLOW}./scripts/stop-test-env.sh${NC}"
    echo
}

# Function to show usage
show_usage() {
    cat << EOF
Usage: $0 [OPTIONS]

Set up test environment for Graph AI with IRIS Community Edition

OPTIONS:
    --skip-odbc          Skip ODBC configuration
    --no-verify          Skip environment verification
    -h, --help           Show this help message

EXAMPLES:
    $0                   # Full setup with verification
    $0 --skip-odbc       # Setup without ODBC configuration
    $0 --no-verify       # Setup without verification

EOF
}

# Parse command line arguments
SKIP_ODBC=false
NO_VERIFY=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-odbc)
            SKIP_ODBC=true
            shift
            ;;
        --no-verify)
            NO_VERIFY=true
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
    print_message "$BLUE" "Setting up Graph AI test environment..."

    # Change to project root
    cd "$PROJECT_ROOT"

    # Execute setup steps
    check_prerequisites
    start_test_db
    init_test_schema
    load_procedures
    load_test_data

    if [[ "$SKIP_ODBC" != true ]]; then
        configure_odbc
    fi

    if [[ "$NO_VERIFY" != true ]]; then
        verify_test_env
    fi

    show_status

    print_message "$GREEN" "Test environment setup completed successfully!"
}

# Error handling
trap 'print_message "$RED" "Script failed on line $LINENO"' ERR

# Run main function
main "$@"