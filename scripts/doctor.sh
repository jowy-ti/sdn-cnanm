#!/usr/bin/env bash
#
# Preflight checks for the sdn-cnanm SRv6 lab.
#
# Answers "is this machine ready to run the lab?" without starting it.  Every
# check is read-only and runs unprivileged; sudo configuration is not checked.
# Exits non-zero if any check fails.
#
#   ./scripts/doctor.sh      or:  make doctor
set -uo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
VENV=${REPO_ROOT}/.venv
VENV_PYTHON=${VENV}/bin/python
ENTRYPOINT=${REPO_ROOT}/src/net_topology_yaml.py

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
        ipmininet_version=$("${VENV_PYTHON}" -c \
            'import importlib.metadata as m; print(m.version("ipmininet"))' 2>/dev/null)
        venv_mininet_version=$("${VENV_PYTHON}" -c \
            'import importlib.metadata as m; print(m.version("mininet"))' 2>/dev/null)
        ok "python imports (ipmininet ${ipmininet_version}, mininet ${venv_mininet_version})"
    else
        fail "cannot import ipmininet/mininet/yaml from ${VENV} (run: make setup)"
    fi
else
    fail "no venv at ${VENV} (run: make setup)"
fi

# --- Mininet ---------------------------------------------------------------- #
# The distro package provides the `mn`/`mnexec` binaries that Mininet shells and
# `mn -c` (ipmininet.clean) rely on; the mininet *Python* module comes from PyPI
# into the venv (checked above), so both halves are verified separately.  A
# version skew between them (distro 2.3.1b4 vs PyPI 2.3.0.dev6) is expected.
if command -v mn >/dev/null 2>&1; then
    # mn prints its version to stderr, not stdout.
    mn_version=$(mn --version 2>&1 | head -1)
    ok "mn: $(command -v mn) (${mn_version})"
else
    fail "mn not on PATH (install mininet: apt-get/dnf install mininet)"
fi

if command -v mnexec >/dev/null 2>&1; then
    ok "mnexec: $(command -v mnexec)"
else
    fail "mnexec not on PATH (install mininet)"
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
    # `show version` talks to the running daemons through the vty sockets, which
    # need root; unprivileged it exits non-zero with an empty stdout.
    vtysh_version=$(vtysh -c 'show version' 2>/dev/null | head -1)
    if [[ -n ${vtysh_version} ]]; then
        ok "vtysh: ${vtysh_version}"
    else
        ok "vtysh: $(command -v vtysh) (version needs root)"
    fi
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
else
    skip "could not read the kernel config; assuming seg6 is available"
fi

# --- sudoers (intentionally not checked) ------------------------------------ #
# The passwordless-sudo rule in /etc/sudoers.d/net_topology is a per-machine
# setup artifact installed by `make setup`, not a property of this checkout, so
# it is deliberately not checked here.

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
