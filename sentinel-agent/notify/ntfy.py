"""ntfy push notification client.

ntfy is the pager — one-way, gets Jim's attention.
Plane is the conversation — two-way, async.

Priority levels:
  5 (urgent): Vault sealed, multiple services down, security from internal IP
  4 (high): Fix failed, PR needs review, compliance regression >= 5
  3 (default): Fix applied, new issue discovered, routine escalation
  2 (low): Heartbeat / cycle clean (max once/hour)
"""

import logging
import time

import requests

# Rate limiting for heartbeat messages
_last_heartbeat: float = 0.0
_HEARTBEAT_INTERVAL = 3600  # 1 hour


def send_ntfy(config: dict, message: str, priority: int = 3,
              title: str = "sentinel-agent",
              tags: list[str] | None = None) -> bool:
    """Send a push notification via ntfy.

    Args:
        config: Agent config dict (needs ntfy.url and ntfy.topic)
        message: Notification body
        priority: 1-5 (maps to ntfy priorities)
        title: Notification title
        tags: Optional list of ntfy tags/emojis

    Returns:
        True if sent successfully
    """
    global _last_heartbeat

    ntfy_cfg = config.get("ntfy", {})
    url = ntfy_cfg.get("url", "")
    topic = ntfy_cfg.get("topic", "sentinel")

    if not url:
        return False

    # Rate-limit heartbeat (priority 2) to once per hour
    if priority <= 2:
        now = time.monotonic()
        if now - _last_heartbeat < _HEARTBEAT_INTERVAL:
            return True  # Silently skip
        _last_heartbeat = now

    headers = {
        "Title": title,
        "Priority": str(priority),
    }

    if tags:
        headers["Tags"] = ",".join(tags)

    # Map priority to tags for mobile display
    if not tags:
        tag_map = {
            5: "rotating_light,skull",
            4: "warning",
            3: "information_source",
            2: "white_check_mark",
            1: "zzz",
        }
        headers["Tags"] = tag_map.get(priority, "robot_face")

    try:
        resp = requests.post(
            f"{url}/{topic}",
            data=message.encode("utf-8"),
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception:
        # ntfy failure should never block the agent cycle
        return False
