import os
import re
import yaml

ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")

def _resolve_env_in_str(value: str) -> str:
    def repl(match):
        var = match.group(1)
        return os.getenv(var, f"<{var}_not_set>")
    return ENV_PATTERN.sub(repl, value)

def _resolve_env(obj):
    if isinstance(obj, dict):
        return {k: _resolve_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env(v) for v in obj]
    if isinstance(obj, str):
        return _resolve_env_in_str(obj)
    return obj

def load_agent_config(path: str = "aurelio_agent.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return _resolve_env(data)
