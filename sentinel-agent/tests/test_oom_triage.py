"""Tests for OPS-986: cgroup vs host OOM triage.

Covers:
- _classify_kernel_oom() helper in sources/wazuh.py
- rules_only_diagnosis() in triage.py for rule 5108 signals
"""

import sys
from pathlib import Path

# Add sentinel-agent root to path so imports resolve without a package install
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sources"))

from sources.wazuh import _classify_kernel_oom
from models import Signal, SignalSource, Tier
from triage import rules_only_diagnosis


# ---------------------------------------------------------------------------
# _classify_kernel_oom
# ---------------------------------------------------------------------------

class TestClassifyKernelOom:
    """Unit tests for the log-line parser."""

    def test_cgroup_oom_with_constraint_memcg_and_path(self):
        """oom-kill line with CONSTRAINT_MEMCG and oom_memcg path."""
        log = (
            "May 26 13:17:56 pve4 kernel: oom-kill:constraint=CONSTRAINT_MEMCG,"
            "nodemask=(null),cpuset=/,mems_allowed=0,"
            "oom_memcg=/lxc/203/system.slice/frigate.service,"
            "task_memcg=/lxc/203/system.slice/frigate.service,"
            "task=frigate.capture,pid=2231002,uid=0"
        )
        is_cgroup, path = _classify_kernel_oom(log)
        assert is_cgroup is True
        assert path == "/lxc/203/system.slice/frigate.service"

    def test_cgroup_oom_memory_cgroup_line(self):
        """'Memory cgroup out of memory:' line from OPS-959 full_log."""
        log = (
            "May 26 13:17:56 pve4 kernel: Memory cgroup out of memory: "
            "Killed process 2231002 (frigate.capture) total-vm:5855076kB,"
            "anon-rss:347820kB,file-rss:4kB,shmem-rss:0kB,"
            "UID:0 pgtables:748kB oom_score_adj:0"
        )
        is_cgroup, path = _classify_kernel_oom(log)
        assert is_cgroup is True
        assert path == ""  # no oom_memcg= in this log variant

    def test_cgroup_oom_with_oom_memcg_only(self):
        """Log line containing oom_memcg= but not the full constraint field."""
        log = (
            "kernel: oom_memcg=/lxc/105/memory.limit_in_bytes "
            "task=some.process,pid=12345,uid=1000"
        )
        is_cgroup, path = _classify_kernel_oom(log)
        assert is_cgroup is True
        assert path == "/lxc/105/memory.limit_in_bytes"

    def test_host_oom_constraint_none(self):
        """oom-kill line with CONSTRAINT_NONE → host-level OOM."""
        log = (
            "May 26 13:17:56 pve4 kernel: oom-kill:constraint=CONSTRAINT_NONE,"
            "nodemask=(null),cpuset=/,mems_allowed=0,"
            "global_oom,task=some.process,pid=99999,uid=0"
        )
        is_cgroup, path = _classify_kernel_oom(log)
        assert is_cgroup is False
        assert path == ""

    def test_host_oom_no_cgroup_markers(self):
        """Generic 'Out of memory' without cgroup markers → host OOM."""
        log = (
            "May 26 13:17:56 pve4 kernel: Out of memory: Kill process 5678 "
            "(some-daemon) score 450 or sacrifice child"
        )
        is_cgroup, path = _classify_kernel_oom(log)
        assert is_cgroup is False
        assert path == ""

    def test_empty_log(self):
        """Empty log string — should not raise and defaults to host OOM."""
        is_cgroup, path = _classify_kernel_oom("")
        assert is_cgroup is False
        assert path == ""

    def test_trailing_comma_stripped_from_path(self):
        """oom_memcg= value may be followed by comma — should be stripped."""
        log = "kernel: oom_memcg=/lxc/203, pid=1234"
        is_cgroup, path = _classify_kernel_oom(log)
        assert is_cgroup is True
        assert path == "/lxc/203"


# ---------------------------------------------------------------------------
# rules_only_diagnosis — rule 5108 branch
# ---------------------------------------------------------------------------

def _make_5108_signal(is_cgroup_oom: bool, cgroup_path: str = "",
                      severity: int = 4) -> Signal:
    """Build a Signal as wazuh.py would produce it for rule 5108."""
    if is_cgroup_oom:
        summary = (
            f"Wazuh alert: [5108] cgroup OOM (contained) — agent: pve4"
            + (f", cgroup: {cgroup_path}" if cgroup_path else "")
        )
    else:
        summary = (
            "Wazuh alert: [5108] System running out of memory. "
            "Availability of the system is in risk. (level 12, agent: pve4)"
        )
        severity = 12

    raw: dict = {
        "rule_id": "5108",
        "rule_level": severity,
        "rule_description": "System running out of memory. Availability of the system is in risk.",
        "rule_groups": ["syslog", "linuxkernel", "service_availability"],
        "agent_id": "009",
        "agent_name": "pve4-alienware",
        "agent_ip": "192.168.12.60",
        "timestamp": "2026-05-26T13:17:57.784+0000",
        "full_log": "May 26 13:17:56 pve4 kernel: ...",
        "source_type": "indexer",
        "is_cgroup_oom": is_cgroup_oom,
    }
    if is_cgroup_oom:
        raw["oom_cgroup_path"] = cgroup_path
        raw["categories"] = ["cat-observability"]

    return Signal(
        source=SignalSource.WAZUH,
        source_id=f"wazuh-alert-5108-pve4-alienware",
        summary=summary,
        severity=severity,
        raw_data=raw,
    )


class TestOomTriage:
    """Integration tests: wazuh-sourced rule-5108 signals through triage."""

    def test_cgroup_oom_skipped(self):
        """Cgroup OOM → Tier.SKIP (contained; no host action needed)."""
        signal = _make_5108_signal(
            is_cgroup_oom=True,
            cgroup_path="/lxc/203/system.slice/frigate.service",
            severity=4,
        )
        result = rules_only_diagnosis(signal)
        assert result == Tier.SKIP, (
            f"Expected SKIP for cgroup OOM but got {result}"
        )

    def test_host_oom_escalated(self):
        """Host OOM (CONSTRAINT_NONE) → Tier.ESCALATE."""
        signal = _make_5108_signal(is_cgroup_oom=False, severity=12)
        result = rules_only_diagnosis(signal)
        assert result == Tier.ESCALATE, (
            f"Expected ESCALATE for host OOM but got {result}"
        )

    def test_cgroup_oom_severity_downgraded_at_source(self):
        """Signal produced from a cgroup OOM has severity <= 5."""
        signal = _make_5108_signal(
            is_cgroup_oom=True,
            cgroup_path="/lxc/203",
            severity=4,
        )
        assert signal.severity <= 5, (
            f"Expected severity <=5 for cgroup OOM, got {signal.severity}"
        )

    def test_host_oom_severity_unchanged(self):
        """Signal produced from a host OOM retains the original rule level."""
        signal = _make_5108_signal(is_cgroup_oom=False, severity=12)
        assert signal.severity == 12

    def test_raw_data_contains_cgroup_path(self):
        """Cgroup OOM signal carries oom_cgroup_path in raw_data."""
        signal = _make_5108_signal(
            is_cgroup_oom=True,
            cgroup_path="/lxc/203/system.slice/frigate.service",
            severity=4,
        )
        assert signal.raw_data.get("oom_cgroup_path") == (
            "/lxc/203/system.slice/frigate.service"
        )

    def test_raw_data_contains_cat_observability(self):
        """Cgroup OOM signal is tagged cat-observability."""
        signal = _make_5108_signal(
            is_cgroup_oom=True,
            cgroup_path="/lxc/203",
            severity=4,
        )
        assert "cat-observability" in signal.raw_data.get("categories", [])
