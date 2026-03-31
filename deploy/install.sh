#!/usr/bin/env bash
# install.sh — Deploy all Ally X agents as systemd services/timers
# Run as root: sudo bash deploy/install.sh
set -euo pipefail

AGENT_USER="${AGENT_USER:-jesse}"
INSTALL_DIR="/home/${AGENT_USER}/allyx-agents"
SYSTEMD_DIR="/etc/systemd/system"
TEMPLATES_DIR="${INSTALL_DIR}/deploy/templates"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
info()  { echo "[INFO]  $*"; }
error() { echo "[ERROR] $*" >&2; exit 1; }

require_root() {
    [[ "$(id -u)" -eq 0 ]] || error "Must be run as root. Use: sudo bash deploy/install.sh"
}

install_service() {
    local agent="$1"
    local service_name="allyx-${agent//_/-}"
    local unit_file="${SYSTEMD_DIR}/${service_name}.service"

    info "Installing service: ${service_name}"
    sed "s/%i/${agent}/g" "${TEMPLATES_DIR}/agent.service" > "${unit_file}"
    chmod 644 "${unit_file}"
}

install_timer() {
    local agent="$1"
    local service_name="allyx-${agent//_/-}"
    local timer_file="${SYSTEMD_DIR}/${service_name}.timer"
    local template="${TEMPLATES_DIR}/${agent}.timer"

    # Fall back to generic template if agent-specific one doesn't exist
    [[ -f "${template}" ]] || template="${TEMPLATES_DIR}/agent.timer"

    info "Installing timer: ${service_name}"
    sed "s/%i/${agent}/g" "${template}" > "${timer_file}"

    # Fix service name reference in timer
    sed -i "s/Description=Timer for %i/Description=Timer for ${agent}/" "${timer_file}"
    chmod 644 "${timer_file}"
}

# ---------------------------------------------------------------------------
# Agents with timers (scheduled, not always-running)
# ---------------------------------------------------------------------------
TIMER_AGENTS=(
    "calendar_sync"
    "morning_digest"
    "commute_ping"
    "grocery_optimizer"
)

# ---------------------------------------------------------------------------
# Agents as persistent services (long-running bots)
# ---------------------------------------------------------------------------
SERVICE_AGENTS=(
    "discord_reminders"
)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
require_root

info "Installing Ally X agents for user: ${AGENT_USER}"
info "Install dir: ${INSTALL_DIR}"

# Create log directory
mkdir -p "${INSTALL_DIR}/logs"
chown "${AGENT_USER}:${AGENT_USER}" "${INSTALL_DIR}/logs"

# Install timer-based agents
for agent in "${TIMER_AGENTS[@]}"; do
    if [[ -d "${INSTALL_DIR}/agents/${agent}" ]]; then
        install_service "${agent}"
        install_timer "${agent}"

        service_name="allyx-${agent//_/-}"
        systemctl daemon-reload
        systemctl enable --now "${service_name}.timer"
        info "Enabled timer: ${service_name}.timer"
    else
        info "Skipping ${agent} (directory not found)"
    fi
done

# Install persistent services
for agent in "${SERVICE_AGENTS[@]}"; do
    if [[ -d "${INSTALL_DIR}/agents/${agent}" ]]; then
        install_service "${agent}"

        service_name="allyx-${agent//_/-}"
        systemctl daemon-reload
        systemctl enable --now "${service_name}.service"
        info "Enabled service: ${service_name}.service"
    else
        info "Skipping ${agent} (directory not found)"
    fi
done

echo ""
info "Installation complete. Status overview:"
for agent in "${TIMER_AGENTS[@]}" "${SERVICE_AGENTS[@]}"; do
    service_name="allyx-${agent//_/-}"
    status=$(systemctl is-active "${service_name}.service" 2>/dev/null || echo "unknown")
    info "  ${service_name}: ${status}"
done

echo ""
info "To view logs:  journalctl -u allyx-<agent> -f"
info "To check timers: systemctl list-timers 'allyx-*'"
