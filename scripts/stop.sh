#!/usr/bin/env bash
# V-Watch — Stop all services
echo "Stopping V-Watch services..."
docker compose --profile full down
echo "All services stopped."
