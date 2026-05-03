#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# V-Watch CCTV AI System — Production Start Script
# ─────────────────────────────────────────────────────────────────────────────

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'

PROFILE="${1:-core}"   # core | edge | full

echo ""
echo -e "${BLUE}╔═══════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║       V-Watch CCTV AI System — Production Start       ║${NC}"
echo -e "${BLUE}╚═══════════════════════════════════════════════════════╝${NC}"
echo ""

# Ensure .env exists
if [ ! -f .env ]; then
    cp .env.example .env
    echo -e "${YELLOW}⚠  Created .env from .env.example — review before production use${NC}"
fi

echo -e "${CYAN}Profile: ${PROFILE}${NC}"
echo ""

case "$PROFILE" in
  core)
    echo -e "${GREEN}▶ Starting core services (postgres + backend + relay + frontend)${NC}"
    docker compose up -d --build
    ;;
  edge)
    echo -e "${GREEN}▶ Starting core + edge AI services${NC}"
    docker compose --profile edge up -d --build
    ;;
  full)
    echo -e "${GREEN}▶ Starting ALL services including watchdog${NC}"
    docker compose --profile full up -d --build
    ;;
  *)
    echo -e "${RED}Unknown profile: $PROFILE${NC}"
    echo "Usage: $0 [core|edge|full]"
    exit 1
    ;;
esac

echo ""
echo -e "${CYAN}Waiting for services to become healthy...${NC}"
sleep 10

echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════${NC}"
echo -e "  Service URLs:"
echo -e "  ${GREEN}Frontend:     http://localhost:${FRONTEND_PORT:-3000}${NC}"
echo -e "  ${GREEN}Backend API:  http://localhost:${BACKEND_PORT:-8000}${NC}"
echo -e "  ${GREEN}API Docs:     http://localhost:${BACKEND_PORT:-8000}/docs${NC}"
echo -e "  ${GREEN}Stream Relay: http://localhost:${RELAY_PORT:-8002}/stream/CAM_001${NC}"
if [ "$PROFILE" = "edge" ] || [ "$PROFILE" = "full" ]; then
echo -e "  ${GREEN}Edge AI:      http://localhost:${EDGE_AI_PORT:-8001}/health${NC}"
fi
if [ "$PROFILE" = "full" ]; then
echo -e "  ${GREEN}Watchdog:     http://localhost:${WATCHDOG_PORT:-9090}/status${NC}"
fi
echo -e "${BLUE}═══════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  Default login: ${YELLOW}admin / Admin@123!${NC}"
echo ""
echo -e "  Run health check: ${CYAN}./scripts/healthcheck.sh${NC}"
echo -e "  View logs:        ${CYAN}docker compose logs -f <service>${NC}"
echo -e "  Stop all:         ${CYAN}docker compose down${NC}"
echo ""
