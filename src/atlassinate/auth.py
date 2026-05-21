import os
import stat
from pathlib import Path

import requests
import yaml

CONFIG_PATH = Path.home() / ".confluence-sync" / "config.yaml"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"No config found at {CONFIG_PATH}. Run 'confluence-sync auth' first.")
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def save_config(instance_url: str, email: str, api_token: str):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        yaml.dump({"instance_url": instance_url, "email": email, "api_token": api_token}, f)
    os.chmod(CONFIG_PATH, stat.S_IRUSR | stat.S_IWUSR)


def validate_credentials(instance_url: str, email: str, api_token: str) -> str:
    url = f"https://{instance_url}/wiki/api/v2/spaces?limit=1"
    response = requests.get(url, auth=(email, api_token), timeout=10)
    if response.status_code == 401:
        raise ValueError("Invalid credentials — check your email and API token.")
    if response.status_code == 403:
        raise ValueError("Access denied — your account may not have Confluence access.")
    response.raise_for_status()

    me = requests.get(
        f"https://{instance_url}/wiki/rest/api/user/current",
        auth=(email, api_token),
        timeout=10,
    )
    me.raise_for_status()
    return me.json().get("displayName", email)
