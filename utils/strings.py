import json
import os
import logging
from typing import Any, Dict

log = logging.getLogger("nopmsbot")

_texts_cache: Dict[str, Any] = {}

def load_texts():
    global _texts_cache
    # Look for branding/texts.json relative to this file's directory
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base_dir, "branding", "texts.json")
    
    if not os.path.exists(path):
        # Fallback to absolute paths if local fails
        path = "/opt/nopmsbot-v2/branding/texts.json"
    
    try:
        with open(path, "r", encoding="utf-8") as f:
            _texts_cache = json.load(f)
        log.info("Branding texts loaded successfully.")
    except Exception as e:
        log.error("Failed to load branding texts: %s", e)
        _texts_cache = {}

def get_text(key_path: str, default: str = "", **kwargs) -> str:
    """
    Fetch a text string by path (e.g. 'settings.status_on').
    Supports placeholder formatting: get_text("settings.current", bot_name="Mielle")
    """
    if not _texts_cache:
        load_texts()
    
    keys = key_path.split(".")
    val = _texts_cache
    try:
        for k in keys:
            val = val[k]
        
        # Inject global bot_name if available
        if "bot_name" not in kwargs and "bot_name" in _texts_cache:
            kwargs["bot_name"] = _texts_cache["bot_name"]
            
        return val.format(**kwargs) if isinstance(val, str) else str(val)
    except Exception:
        return default or key_path
