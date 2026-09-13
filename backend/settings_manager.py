import os
import json

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), 'settings.json')

DEFAULT_SETTINGS = {
    "account_balance": 10000.0,
    "risk_percentage": 1.0,
    "min_tp_pips": 120.0,
    "max_tp_pips": 250.0,
    "max_sl_pips": 90.0,
    "min_sl_pips": 50.0,
    "be_trigger_pips": 70.0,
    "partial_tp_enabled": True,
    "partial_tp_pips": 100.0,
    "partial_tp_ratio": 0.5,
    "max_daily_trades": 2
}

def load_settings() -> dict:
    if not os.path.exists(SETTINGS_FILE):
        save_settings(DEFAULT_SETTINGS)
        return DEFAULT_SETTINGS
    try:
        with open(SETTINGS_FILE, 'r') as f:
            data = json.load(f)
            # Ensure defaults for missing keys
            for k, v in DEFAULT_SETTINGS.items():
                if k not in data:
                    data[k] = v
            return data
    except Exception as e:
        print(f"Error loading settings: {e}")
        return DEFAULT_SETTINGS

def save_settings(settings: dict):
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(settings, f, indent=4)
    except Exception as e:
        print(f"Error saving settings: {e}")
