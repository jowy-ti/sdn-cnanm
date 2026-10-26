#!/usr/bin/env bash
#
# Bootstrap a machine for the sdn-cnanm SRv6 lab.
#
# Run as the user that will operate the lab (NOT as root; the script sudo's
# only for the steps that need it, so the Python venv stays owned by you):
#
#   ./scripts/setup.sh              # OS packages + venv + sudoers rule
#   ./scripts/setup.sh --dry-run    # print what would be done
#   ./scripts/setup.sh --runtime-only
#                                   # skip the dev tooling (ruff/pyright/MCP)
#
# Idempotent: re-running it is a no-op on a machine already set up.
#
# What it does:
#   1. installs the OS packages (frr, iproute2, procps, psmisc, tcpdump, ...)
#      through apt-get on Debian/Ubuntu or dnf on Fedora/RHEL;
#   2. detects where the distro put the FRR daemons (/usr/lib/frr vs
#      /usr/libexec/frr) and reports it -- the entrypoint discovers it too;
#   3. stops and disables the system frr.service (this lab starts its own
#      FRR instances inside the Mininet namespaces);
#   4. adds root to the frrvty group (ipmininet validates daemon configs as
#      root; FRR aborts otherwise);
#   5. checks the kernel was built with CONFIG_IPV6_SEG6_LWTUNNEL;
#   6. creates <repo>/.venv and installs requirements-dev.txt (or
#      requirements.txt with --runtime-only);
#   7. writes /etc/sudoers.d/net_topology with NOPASSWD entries for the two
#      exact command lines the lab needs (validated with visudo first);
#   8. prints a summary and the next commands.
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
VENV=${REPO_ROOT}/.venv
VENV_PYTHON=${VENV}/bin/python
ENTRYPOINT=${REPO_ROOT}/src/net_topology_yaml.py
SUDOERS_FILE=/etc/sudoers.d/net_topology

DRY_RUN=false
RUNTIME_ONLY=false

usage() {
    sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while (($# > 0)); do
    case $1 in
        --dry-run) DRY_RUN=true ;;
        --runtime-only) RUNTIME_ONLY=true ;;
        -h | --help) usage; exit 0 ;;
        *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
    esac
    shift
done

step() { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '    warning: %s\n' "$*" >&2; }
die() {
    printf '\nERROR: %s\n' "$*" >&2
    exit 1
}

run() {
    if $DRY_RUN; then
        printf '    would run: %s\n' "$*"
    else
        "$@"
    fi
}

# Privilege escalation.  The script must be run as a normal user; only the
# system-level steps go through sudo.
if [[ ${EUID} -eq 0 ]]; then
    die "run this as your normal user, not root: the Python venv must not be root-owned. It sudo's internally."
fi
command -v sudo >/dev/null 2>&1 || die "sudo is required but was not found"

if [[ ${REPO_ROOT} == *[[:space:]]* ]]; then
    die "the repository path contains whitespace (${REPO_ROOT}); the sudoers rule matches the exact argv, and escaping spaces is fragile. Move the checkout to a path without spaces."
fi

# --------------------------------------------------------------------------- #
# 1. Operating-system packages
# --------------------------------------------------------------------------- #
APT_PACKAGES=(
    frr iproute2 iputils-ping procps psmisc tcpdump
    curl ca-certificates make python3 python3-venv python3-pip
)
DNF_PACKAGES=(
    frr iproute iputils procps-ng psmisc tcpdump
    curl ca-certificates make python3 python3-pip
)

install_packages() {
    if command -v apt-get >/dev/null 2>&1; then
        step "Installing OS packages with apt-get"
        run sudo env DEBIAN_FRONTEND=noninteractive apt-get update -q
        run sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -q "${APT_PACKAGES[@]}"
    elif command -v dnf >/dev/null 2>&1; then
        step "Installing OS packages with dnf"
        run sudo dnf install -y "${DNF_PACKAGES[@]}"
    else
        warn "no apt-get or dnf found; install these packages yourself:"
        info "${APT_PACKAGES[*]}"
    fi
}

# --------------------------------------------------------------------------- #
# 2. FRR daemon directory
# --------------------------------------------------------------------------- #
FRR_DAEMON_DIR=
detect_frr_dir() {
    local dir
    for dir in /usr/lib/frr /usr/libexec/frr /usr/local/lib/frr; do
        if [[ -x ${dir}/zebra || -x ${dir}/ospf6d ]]; then
            FRR_DAEMON_DIR=${dir}
            return 0
        fi
    done
    return 1
}

frr_version() {
    if command -v vtysh >/dev/null 2>&1; then
        vtysh -c 'show version' 2>/dev/null |
            sed -n 's/^FRR \([^ ]*\).*/\1/p' | head -1
    fi
}

# --------------------------------------------------------------------------- #
# 3. Kernel SRv6 support
# --------------------------------------------------------------------------- #
check_seg6() {
    local config=/boot/config-$(uname -r)
    if [[ -r ${config} ]] && grep -q '^CONFIG_IPV6_SEG6_LWTUNNEL=y' "${config}"; then
        return 0
    fi
    if [[ -r /proc/config.gz ]] && zgrep -q '^CONFIG_IPV6_SEG6_LWTUNNEL=y' /proc/config.gz; then
        return 0
    fi
    # No readable config: fall back to asking the running kernel directly.
    if sudo ip sr tunsrc show >/dev/null 2>&1; then
        return 0
    fi
    return 1
}

# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
install_packages

step "Detecting the FRR daemon directory"
if detect_frr_dir; then
    info "found ${FRR_DAEMON_DIR}"
else
    warn "no FRR daemons found in /usr/lib/frr or /usr/libexec/frr."
    warn "Install FRR (apt-get install frr / dnf install frr) and re-run."
fi

step "Stopping and disabling the system frr.service"
if systemctl list-unit-files frr.service >/dev/null 2>&1; then
    run sudo systemctl disable --now frr || warn "could not disable frr.service"
else
    info "frr.service not present; nothing to do"
fi

step "Adding root to the frrvty group"
if getent group frrvty >/dev/null 2>&1; then
    if id -nG root | tr ' ' '\n' | grep -qx frrvty; then
        info "root is already in frrvty"
    else
        run sudo usermod -aG frrvty root
        info "added; this only affects new processes, which is what ipmininet starts"
    fi
else
    warn "group frrvty does not exist; is FRR installed?"
fi

step "Checking kernel SRv6 support"
if check_seg6; then
    info "CONFIG_IPV6_SEG6_LWTUNNEL is available"
else
    warn "could not confirm CONFIG_IPV6_SEG6_LWTUNNEL; SRv6 routes may fail"
fi

step "Creating the Python virtual environment"
if [[ -x ${VENV_PYTHON} ]]; then
    info "${VENV} already exists"
else
    run python3 -m venv "${VENV}"
fi
run "${VENV_PYTHON}" -m pip install --quiet --upgrade pip
if $RUNTIME_ONLY; then
    run "${VENV_PYTHON}" -m pip install --quiet -r "${REPO_ROOT}/requirements.txt"
else
    run "${VENV_PYTHON}" -m pip install --quiet -r "${REPO_ROOT}/requirements-dev.txt"
fi

step "Writing ${SUDOERS_FILE}"
# The lab needs root, and the rule deliberately matches the exact absolute
# argv (no extra arguments, no -E; see RUNBOOK.md section 2.3).  --check must
# be run without sudo, so it gets no rule.
SUDOERS_CONTENT=$(
    cat <<EOF
# Generated by sdn-cnanm/scripts/setup.sh -- re-run the script after moving the
# checkout or recreating .venv.
Defaults:$(id -un) env_keep += "TERM"
$(id -un) ALL=(ALL) NOPASSWD: ${VENV_PYTHON} ${ENTRYPOINT}
$(id -un) ALL=(ALL) NOPASSWD: ${VENV_PYTHON} -m ipmininet.clean
EOF
)

if $DRY_RUN; then
    printf '%s\n' "${SUDOERS_CONTENT}" | sed 's/^/    | /'
    info "would validate with visudo and install as ${SUDOERS_FILE} (0440)"
else
    tmp=$(mktemp)
    trap 'rm -f "${tmp}"' EXIT
    printf '%s\n' "${SUDOERS_CONTENT}" >"${tmp}"
    sudo visudo -cqf "${tmp}" || die "generated sudoers file failed validation"
    sudo install -o root -g root -m 0440 "${tmp}" "${SUDOERS_FILE}"
    info "installed (0440 root:root)"
fi

# --------------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------------- #
step "Summary"
info "repository:        ${REPO_ROOT}"
info "python:            $(python3 --version 2>&1)"
info "venv:              ${VENV}"
if detect_frr_dir; then
    info "FRR daemons:       ${FRR_DAEMON_DIR}"
else
    info "FRR daemons:       NOT FOUND"
fi
info "FRR version:       $(frr_version || echo unknown)"
info "sudoers:           ${SUDOERS_FILE}"
if check_seg6; then
    info "kernel seg6:       available"
else
    info "kernel seg6:       could not confirm"
fi

cat <<EOF

Next steps:
    make doctor    # preflight checks
    make check     # parse/validate topologies/srv6.yaml (no root)
    make run       # start the lab; 'exit' at the mininet> prompt tears it down
EOF
