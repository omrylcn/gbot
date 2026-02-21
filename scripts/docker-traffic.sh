#!/bin/bash
# Docker Container Traffic Monitor
# Shows outbound connections for each container with DNS resolution
#
# Usage:
#   ./scripts/docker-traffic.sh          # snapshot
#   ./scripts/docker-traffic.sh --watch   # live (every 5s)
#   ./scripts/docker-traffic.sh --log     # enable iptables logging

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Get container name from IP
get_container_name() {
    local ip="$1"
    docker network inspect $(docker network ls -q) 2>/dev/null \
        | python3 -c "
import json, sys
data = json.load(sys.stdin)
for net in data:
    for cid, info in net.get('Containers', {}).items():
        addr = info.get('IPv4Address', '').split('/')[0]
        if addr == '$ip':
            print(info.get('Name', cid[:12]))
            sys.exit(0)
print('host')
" 2>/dev/null || echo "unknown"
}

# Resolve IP to hostname (cached)
declare -A DNS_CACHE
resolve_ip() {
    local ip="$1"
    if [[ -n "${DNS_CACHE[$ip]:-}" ]]; then
        echo "${DNS_CACHE[$ip]}"
        return
    fi
    local host
    host=$(timeout 2 dig +short -x "$ip" 2>/dev/null | head -1 | sed 's/\.$//')
    if [[ -z "$host" ]]; then
        host="$ip"
    fi
    DNS_CACHE[$ip]="$host"
    echo "$host"
}

# Show connections for a single container
show_container_connections() {
    local container="$1"
    local pid
    pid=$(docker inspect "$container" --format '{{.State.Pid}}' 2>/dev/null)
    if [[ -z "$pid" || "$pid" == "0" ]]; then
        return
    fi

    echo -e "\n${CYAN}━━━ $container ━━━${NC}"

    # Get established connections via nsenter
    local connections
    connections=$(nsenter -t "$pid" -n ss -tnp 2>/dev/null | grep ESTAB || true)

    if [[ -z "$connections" ]]; then
        echo -e "  ${GREEN}No outbound connections${NC}"
        return
    fi

    echo -e "  ${YELLOW}%-6s %-40s %-40s${NC}" "Proto" "Local" "Remote"
    echo "$connections" | while IFS= read -r line; do
        local local_addr remote_addr
        local_addr=$(echo "$line" | awk '{print $4}')
        remote_addr=$(echo "$line" | awk '{print $5}')

        # Extract remote IP for DNS
        local remote_ip
        remote_ip=$(echo "$remote_addr" | sed 's/:[0-9]*$//' | sed 's/^\[//;s/\]$//')

        # Skip Docker internal
        if [[ "$remote_ip" == 172.* ]]; then
            local target
            target=$(get_container_name "$remote_ip")
            echo -e "  tcp    ${local_addr}  →  ${GREEN}${target}${NC} (${remote_addr})"
        else
            local hostname
            hostname=$(resolve_ip "$remote_ip")
            echo -e "  tcp    ${local_addr}  →  ${RED}${hostname}${NC} (${remote_addr})"
        fi
    done
}

# Enable iptables logging for Docker traffic
enable_logging() {
    echo -e "${YELLOW}Enabling iptables logging for Docker containers...${NC}"

    # Clean old rules
    iptables -C DOCKER-USER -j LOG --log-prefix "DOCKER-OUT: " --log-level 4 2>/dev/null \
        && iptables -D DOCKER-USER -j LOG --log-prefix "DOCKER-OUT: " --log-level 4 2>/dev/null

    # Log new outbound connections (SYN only — not every packet)
    iptables -I DOCKER-USER -m conntrack --ctstate NEW -j LOG \
        --log-prefix "DOCKER-OUT: " --log-level 4

    echo -e "${GREEN}Logging enabled. View with:${NC}"
    echo "  journalctl -f | grep 'DOCKER-OUT'"
    echo "  # or"
    echo "  tail -f /var/log/syslog | grep 'DOCKER-OUT'"
    exit 0
}

# Disable iptables logging
disable_logging() {
    iptables -D DOCKER-USER -m conntrack --ctstate NEW -j LOG \
        --log-prefix "DOCKER-OUT: " --log-level 4 2>/dev/null && \
        echo -e "${GREEN}Logging disabled.${NC}" || \
        echo -e "${YELLOW}No logging rules found.${NC}"
    exit 0
}

# Main
case "${1:-}" in
    --log)
        enable_logging
        ;;
    --no-log)
        disable_logging
        ;;
    --watch)
        while true; do
            clear
            echo -e "${CYAN}Docker Container Traffic Monitor${NC} — $(date '+%H:%M:%S')"
            echo -e "${CYAN}════════════════════════════════════════${NC}"
            for container in $(docker ps --format '{{.Names}}'); do
                show_container_connections "$container"
            done
            sleep 5
        done
        ;;
    *)
        echo -e "${CYAN}Docker Container Traffic Monitor${NC} — $(date '+%H:%M:%S')"
        echo -e "${CYAN}════════════════════════════════════════${NC}"
        for container in $(docker ps --format '{{.Names}}'); do
            show_container_connections "$container"
        done
        echo ""
        echo -e "Tip: ${YELLOW}$0 --watch${NC} for live monitoring"
        echo -e "     ${YELLOW}$0 --log${NC}   to enable iptables logging"
        ;;
esac
