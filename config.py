import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default)


DEFAULTS = {
    "symbol": _get("SCAN_SYMBOL", "BTCUSDT"),
    "timeframes": _get("TIMEFRAMES", "1m,3m,5m,15m"),
    "min_confidence": int(_get("MIN_CONFIDENCE", "80")),
    "tf_min_confidence": int(_get("TF_MIN_CONFIDENCE", "70")),
    "min_agreeing_strategies": int(_get("MIN_AGREEING_STRATEGIES", "2")),
    "cooldown_minutes": int(_get("COOLDOWN_MINUTES", "15")),
    "scan_interval_sec": int(_get("SCAN_INTERVAL_SEC", "60")),
    "report_interval_sec": int(_get("REPORT_INTERVAL_SEC", "0")),
}

TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_USER_ID = _get("TELEGRAM_USER_ID", "")


class Config:
    """AI fields baraye Brain (mesle XT). Scanner settings tu DEFAULTS/settings.json."""

    AI_PROVIDER: str = _get("AI_PROVIDER", "auto").lower().strip()
    AI_API_KEY: str = _get("AI_API_KEY", "")
    AI_API_KEYS: str = _get("AI_API_KEYS", "")
    AI_BASE_URL: str = _get("AI_BASE_URL", "https://api.openai.com/v1")
    AI_MODEL: str = _get("AI_MODEL", "")

    ANTHROPIC_API_KEY: str = _get("ANTHROPIC_API_KEY", "")
    ANTHROPIC_BASE_URL: str = _get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    ANTHROPIC_MODEL: str = _get("ANTHROPIC_MODEL", "")

    AI_FALLBACK_MODELS: str = _get("AI_FALLBACK_MODELS", "")

    AGENT_MAX_STEPS: int = int(_get("AGENT_MAX_STEPS", "6") or 6)
    AGENT_LLM_CONFIRM: str = _get("AGENT_LLM_CONFIRM", "true").lower()  # true = LLM second-opinion ru ALIGNED

    @classmethod
    def get_effective_provider(cls) -> str:
        if cls.AI_PROVIDER == "anthropic":
            return "anthropic"
        if cls.AI_PROVIDER == "openai":
            return "openai"
        if cls.ANTHROPIC_API_KEY and not cls.AI_API_KEY:
            return "anthropic"
        return "openai"

    @classmethod
    def get_effective_api_key(cls) -> str:
        return cls.ANTHROPIC_API_KEY if cls.get_effective_provider() == "anthropic" else cls.AI_API_KEY

    @classmethod
    def get_effective_base_url(cls) -> str:
        return cls.ANTHROPIC_BASE_URL if cls.get_effective_provider() == "anthropic" else cls.AI_BASE_URL

    @classmethod
    def get_effective_model(cls) -> str:
        return cls.ANTHROPIC_MODEL if cls.get_effective_provider() == "anthropic" else cls.AI_MODEL
