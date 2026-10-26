#!/usr/bin/env bash
#
# Preflight checks for the sdn-cnanm SRv6 lab.
#
# Answers "is this machine ready to run the lab?" without starting it.  Every
# check is read-only.  Exits non-zero if any check fails.
#
#   ./scripts/doctor.sh      or:  make doctor
set -uo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
VENV=${REPO_ROOT}/.venv
VENV_PYTHON=${VENV}/bin/python
ENTRYPOINT=${REPO_ROOT}/src/net_topology_yaml.py
SUDOERS_FILE=/etc/sudoers.d/net_topology

failures=0
ok() { printf '  ok    %s\n' "$*"; }
fail() {
    printf '  FAIL  %s\n' "$*"
    failures=$((failures + 1))
}
skip() { printf '  --    %s\n' "$*"; }

echo "sdn-cnanm doctor"

# --- Python environment ----------------------------------------------------- #
if [[ -x ${VENV_PYTHON} ]]; then
    ok "venv: ${VENV}"
    if "${VENV_PYTHON}" -c 'import ipmininet, mininet, yaml' >/dev/null 2>&1; then
        version=$("${VENV_PYTHON}" -c \
            'import importlib.metadata as m; print(m.version("ipmininet"))' 2>/dev/null)
        ok "python imports (ipmininet ${version})"
    else
        fail "cannot import ipmininet/mininet/yaml from ${VENV} (run: make setup)"
    fi
else
    fail "no venv at ${VENV} (run: make setup)"
fi

# --- FRR -------------------------------------------------------------------- #
frr_dir=
for dir in /usr/lib/frr /usr/libexec/frr /usr/local/lib/frr; do
    if [[ -x ${dir}/zebra || -x ${dir}/ospf6d ]]; then
        frr_dir=${dir}
        break
    fi
done
if [[ -n ${frr_dir} ]]; then
    missing=()
    for daemon in zebra ospfd ospf6d; do
        [[ -x ${frr_dir}/${daemon} ]] || missing+=("${daemon}")
    done
    if ((${#missing[@]} == 0)); then
        ok "FRR daemons: ${frr_dir}"
    else
        fail "FRR daemons missing from ${frr_dir}: ${missing[*]}"
    fi
else
    fail "no FRR daemons in /usr/lib/frr or /usr/libexec/frr (install frr)"
fi

if command -v vtysh >/dev/null 2>&1; then
    ok "vtysh: $(vtysh -c 'show version' 2>/dev/null | head -1)"
else
    fail "vtysh not on PATH"
fi

if [[ -e /run/frr ]] || [[ -e /var/run/frr ]]; then
    ok "FRR vty directory exists (/var/run/frr)"
else
    fail "/var/run/frr does not exist (FRR not installed?)"
fi

# --- FRR system service ----------------------------------------------------- #
if command -v systemctl >/dev/null 2>&1 &&
    systemctl list-unit-files frr.service >/dev/null 2>&1; then
    if systemctl is-active --quiet frr.service; then
        fail "frr.service is running; the lab needs it stopped (sudo systemctl disable --now frr)"
    else
        ok "frr.service is not running"
    fi
else
    skip "no frr.service unit"
fi

# --- frrvty ----------------------------------------------------------------- #
if getent group frrvty >/dev/null 2>&1; then
    if id -nG root | tr ' ' '\n' | grep -qx frrvty; then
        ok "root is in the frrvty group"
    else
        fail "root is not in frrvty (sudo usermod -aG frrvty root)"
    fi
else
    fail "group frrvty does not exist"
fi

# --- kernel seg6 ------------------------------------------------------------ #
seg6_ok=false
config=/boot/config-$(uname -r)
if [[ -r ${config} ]] && grep -q '^CONFIG_IPV6_SEG6_LWTUNNEL=y' "${config}"; then
    seg6_ok=true
elif [[ -r /proc/config.gz ]] &&
    zgrep -q '^CONFIG_IPV6_SEG6_LWTUNNEL=y' /proc/config.gz; then
    seg6_ok=true
fi
if $seg6_ok; then
    ok "kernel has CONFIG_IPV6_SEG6_LWTUNNEL"
elif sudo -n ip sr tunsrc show >/dev/null 2>&1; then
    ok "kernel accepts seg6 (ip sr tunsrc)"
else
    skip "could not read the kernel config; assuming seg6 is available"
fi

# --- sudoers ---------------------------------------------------------------- #
if sudo -n test -r "${SUDOERS_FILE}" 2>/dev/null || [[ -r ${SUDOERS_FILE} ]]; then
    ok "sudoers rule: ${SUDOERS_FILE}"
    if sudo -n -l "${VENV_PYTHON}" "${ENTRYPOINT}" >/dev/null 2>&1; then
        ok "passwordless sudo for the lab entrypoint"
    else
        fail "sudoers rule does not match the current argv (re-run: make setup)"
    fi
    if sudo -n -l "${VENV_PYTHON}" -m ipmininet.clean >/dev/null 2>&1; then
        ok "passwordless sudo for ipmininet.clean"
    else
        fail "sudoers rule does not match 'python -m ipmininet.clean' (re-run: make setup)"
    fi
else
    fail "no ${SUDOERS_FILE} (run: make setup)"
fi

# --- topology --------------------------------------------------------------- #
if [[ -x ${VENV_PYTHON} ]]; then
    if "${VENV_PYTHON}" "${ENTRYPOINT}" --check >/dev/null 2>&1; then
        ok "topology parses (topologies/srv6.yaml)"
    else
        fail "topology does not parse (run: make check)"
    fi
fi

echo
if ((failures > 0)); then
    printf '%d check(s) failed.\n' "${failures}"
    exit 1
fi
echo "All checks passed."
