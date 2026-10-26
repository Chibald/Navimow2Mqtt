"""Configuration, read from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


class ConfigError(Exception):
    pass


@dataclass
class MqttConfig:
    host: str = "localhost"
    port: int = 1883
    username: str | None = None
    password: str | None = None
    client_id: str = "navimow2mqtt"
    base_topic: str = "navimow"
    discovery: bool = True
    discovery_prefix: str = "homeassistant"


@dataclass
class NavimowConfig:
    """Cloud endpoints and OAuth client, as used by the official Home Assistant integration."""
    api_url: str = "https://navimow-fra.ninebot.com"
    authorize_url: str = "https://navimow-h5-fra.willand.com/smartHome/login?channel=homeassistant"
    token_url: str = "https://navimow-fra.ninebot.com/openapi/oauth/getAccessToken"
    client_id: str = "homeassistant"
    # Public client secret published in segwaynavimow/NavimowHA (const.py).
    client_secret: str = "57056e15-722e-42be-bbaa-b0cbfb208a52"
    redirect_uri: str = "https://my.home-assistant.io/redirect/oauth"
    token_file: str = "/data/token.json"


@dataclass
class Config:
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    navimow: NavimowConfig = field(default_factory=NavimowConfig)
    http_poll_interval: float = 60.0   # minimum gap between HTTP fallback polls
    mqtt_stale_seconds: float = 90.0   # no cloud push for this long -> fall back to HTTP
    offline_after: float = 300.0       # no data at all for this long -> mower "offline"
    web_port: int = 8099               # login page; 0 disables it
    public_url: str = ""               # where the login page can be reached from your network
    path_max_points: int = 8000        # points kept per mowing session; 0 disables the path
    log_dir: str = "/data/logs"        # event log + app log; empty disables both files
    log_keep_days: int = 14
    log_max_mb: int = 50               # per day, event log
    stats_file: str = ""               # weekly statistics (small JSON file); empty disables them
    stats_weeks: int = 12              # weekly totals kept in that file
    stats_min_session_s: int = 60      # sessions with less mowing time are not counted
    timezone: str = "UTC"              # decides where a week starts (Monday 00:00 local time)
    language: str = "en"               # texts on the sign-in page and the map: en | nl
    maps_dir: str = ""                 # background pictures for the map tile; empty disables them
    trail_file: str = ""               # the last mowed path, kept across restarts; empty = in memory only


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _num(env, name: str, default, cast):
    raw = env.get(name)
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError:
        raise ConfigError("%s=%r is not a valid number" % (name, raw))


def parse(env=None) -> Config:
    env = os.environ if env is None else env
    m = MqttConfig()
    m.host = env.get("MQTT_HOST") or m.host
    m.port = _num(env, "MQTT_PORT", m.port, int)
    m.username = env.get("MQTT_USERNAME") or None
    m.password = env.get("MQTT_PASSWORD") or None
    m.client_id = env.get("MQTT_CLIENT_ID") or m.client_id
    m.base_topic = (env.get("MQTT_BASE_TOPIC") or m.base_topic).strip("/")
    if env.get("MQTT_DISCOVERY"):
        m.discovery = _truthy(env["MQTT_DISCOVERY"])
    m.discovery_prefix = (env.get("MQTT_DISCOVERY_PREFIX") or m.discovery_prefix).strip("/")

    n = NavimowConfig()
    for attr, var in (("api_url", "NAVIMOW_API_URL"), ("authorize_url", "NAVIMOW_AUTHORIZE_URL"),
                      ("token_url", "NAVIMOW_TOKEN_URL"), ("client_id", "NAVIMOW_CLIENT_ID"),
                      ("client_secret", "NAVIMOW_CLIENT_SECRET"),
                      ("redirect_uri", "NAVIMOW_REDIRECT_URI"), ("token_file", "NAVIMOW_TOKEN_FILE")):
        if env.get(var):
            setattr(n, attr, env[var])
    n.api_url = n.api_url.rstrip("/")

    tz_name = (env.get("TIMEZONE") or env.get("TZ") or "UTC").strip()
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(tz_name)
    except Exception as exc:  # noqa: BLE001 - ZoneInfoNotFoundError, ValueError, missing tzdata ...
        raise ConfigError("TIMEZONE=%r is not a known time zone (%s), e.g. Europe/Amsterdam" % (tz_name, exc))

    from .i18n import LANGUAGES, normalize
    language = normalize(env.get("LANGUAGE")) if env.get("LANGUAGE") else "en"
    if language is None:
        raise ConfigError("LANGUAGE=%r is not available (choose from %s)" % (env.get("LANGUAGE"), ", ".join(LANGUAGES)))

    public_url = (env.get("PUBLIC_URL") or "").strip().rstrip("/")
    if public_url and not public_url.lower().startswith(("http://", "https://")):
        raise ConfigError("PUBLIC_URL=%r must start with http:// or https://" % public_url)

    return Config(
        mqtt=m,
        navimow=n,
        http_poll_interval=_num(env, "HTTP_POLL_INTERVAL", 60.0, float),
        mqtt_stale_seconds=_num(env, "MQTT_STALE_SECONDS", 90.0, float),
        offline_after=_num(env, "OFFLINE_AFTER", 300.0, float),
        web_port=_num(env, "WEB_PORT", 8099, int),
        public_url=public_url,
        path_max_points=_num(env, "PATH_MAX_POINTS", 8000, int),
        log_dir=env.get("LOG_DIR", "/data/logs").strip(),
        log_keep_days=_num(env, "LOG_KEEP_DAYS", 14, int),
        log_max_mb=_num(env, "LOG_MAX_MB", 50, int),
        stats_file=(env.get("STATS_FILE") or "").strip(),
        stats_weeks=_num(env, "STATS_WEEKS", 12, int),
        stats_min_session_s=_num(env, "STATS_MIN_SESSION_S", 60, int),
        timezone=tz_name,
        language=language,
        maps_dir=(env.get("MAPS_DIR") or "").strip(),
        trail_file=(env.get("TRAIL_FILE") or "").strip(),
    )
