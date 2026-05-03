#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# V-Watch System Health Check Script
# Run this anytime to verify all services are healthy
# Usage: ./scripts/healthcheck.sh
# ─────────────────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

BACKEND_PORT="${BACKEND_PORT:-8000}"
RELAY_PORT="${RELAY_PORT:-8002}"
EDGE_PORT="${EDGE_AI_PORT:-8001}"
WATCHDOG_PORT="${WATCHDOG_PORT:-9090}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"

pass() { echo -e "  ${GREEN}✅ PASS${NC}  $1"; }
fail() { echo -e "  ${RED}❌ FAIL${NC}  $1"; }
warn() { echo -e "  ${YELLOW}⚠️  WARN${NC}  $1"; }
info() { echo -e "  ${CYAN}ℹ️  INFO${NC}  $1"; }

check_http() {
    local name="$1" url="$2" timeout="${3:-5}"
    local status
    status=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$timeout" "$url" 2>/dev/null)
    if [ "$status" = "200" ] || [ "$status" = "204" ]; then
        pass "$name  ($url) → HTTP $status"
        return 0
    else
        fail "$name  ($url) → HTTP ${status:-timeout}"
        return 1
    fi
}

check_container() {
    local name="$1"
    local state
    state=$(docker inspect --format '{{.State.Status}}' "$name" 2>/dev/null)
    if [ "$state" = "running" ]; then
        pass "Container: $name → running"
        return 0
    else
        fail "Container: $name → ${state:-not found}"
        return 1
    fi
}

echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}          V-Watch System Health Check                  ${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════${NC}"
echo ""

TOTAL=0; PASSED=0; FAILED=0

run_check() {
    TOTAL=$((TOTAL+1))
    if "$@"; then PASSED=$((PASSED+1)); else FAILED=$((FAILED+1)); fi
}

# ── Container States ──────────────────────────────────────────────────────────
echo -e "${CYAN}[1] Docker Container States${NC}"
run_check check_container "vwatch_postgres"
run_check check_container "vwatch_backend"
run_check check_container "vwatch_frontend"
run_check check_container "vwatch_relay"
# Optional containers (don't fail if not started)
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "vwatch_edge"; then
    run_check check_container "vwatch_edge"
fi
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "vwatch_watchdog"; then
    run_check check_container "vwatch_watchdog"
fi
echo ""

# ── Service Health Endpoints ──────────────────────────────────────────────────
echo -e "${CYAN}[2] Service Health Endpoints${NC}"
run_check check_http "Backend API"      "http://localhost:${BACKEND_PORT}/health"
run_check check_http "Stream Relay"    "http://localhost:${RELAY_PORT}/health"
run_check check_http "Frontend"        "http://localhost:${FRONTEND_PORT}"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "vwatch_edge"; then
    run_check check_http "Edge AI"     "http://localhost:${EDGE_PORT}/health"
fi
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "vwatch_watchdog"; then
    run_check check_http "Watchdog"    "http://localhost:${WATCHDOG_PORT}/health"
fi
echo ""

# ── Stream Availability ───────────────────────────────────────────────────────
echo -e "${CYAN}[3] Camera Streams${NC}"
CAM_ID="${CAMERA_ID:-CAM_001}"
SNAP_URL="http://localhost:${RELAY_PORT}/snapshot/${CAM_ID}"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$SNAP_URL" 2>/dev/null)
if [ "$STATUS" = "200" ]; then
    pass "Snapshot: $SNAP_URL → HTTP $STATUS"
    TOTAL=$((TOTAL+1)); PASSED=$((PASSED+1))
else
    warn "Snapshot: $SNAP_URL → HTTP ${STATUS:-timeout} (camera may be starting)"
    TOTAL=$((TOTAL+1))
fi
echo ""

# ── Backend API Detailed ──────────────────────────────────────────────────────
echo -e "${CYAN}[4] Backend API Details${NC}"
HEALTH_RESP=$(curl -s --max-time 5 "http://localhost:${BACKEND_PORT}/health" 2>/dev/null)
if [ -n "$HEALTH_RESP" ]; then
    info "Backend health: $HEALTH_RESP"
else
    warn "Could not fetch backend health details"
fi
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────
echo -e "${BLUE}═══════════════════════════════════════════════════════${NC}"
echo -e "  Total: ${TOTAL}  |  ${GREEN}Passed: ${PASSED}${NC}  |  ${RED}Failed: ${FAILED}${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════${NC}"
echo ""

if [ "$FAILED" -gt 0 ]; then
    echo -e "${RED}⚠️  Some checks failed. Run: docker compose logs --tail=50 <service>${NC}"
    exit 1
else
    echo -e "${GREEN}✅ All checks passed. System is healthy.${NC}"
    exit 0
fi
