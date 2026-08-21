import os
from pathlib import Path
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

load_dotenv()

TIMEZONE = ZoneInfo('America/Sao_Paulo')

BASE_DIR = Path(__file__).resolve().parent.parent

def clean_env(key, default=''):
    val = os.getenv(key) or os.getenv(key.upper()) or os.getenv(key.lower())
    if not val:
        return default
    return str(val).strip().strip('"').strip("'").strip()

API_KEYS = {
    'mainnet': {
        'key': clean_env('mainnet_api_key', ''),
        'secret': clean_env('mainnet_secret_key', ''),
    },
    'testnet_spot': {
        'key': clean_env('testnet_spot_api_key', ''),
        'secret': clean_env('testnet_spot_secret_key', ''),
    },
    'testnet_futures': {
        'key': clean_env('testnet_futures_api_key', ''),
        'secret': clean_env('testnet_futures_secret_key', ''),
    },
    'gemini': clean_env('GEMINI_API_KEY') or clean_env('gemini_api_key') or clean_env('gemini_api') or clean_env('GEMINI_KEY') or clean_env('gemini') or ''
}

TELEGRAM_CONFIG = {
    'bot_token': clean_env('bot_token', ''),
    'chat_id': clean_env('chat_id', '')
}

DASHBOARD_CONFIG = {
    'user': clean_env('DASHBOARD_USER', 'admin'),
    'password': clean_env('DASHBOARD_PASSWORD', 'admin123'),
    'port': int(clean_env('PORT', '8080')),
    'secret_key': clean_env('SECRET_KEY') or clean_env('DASHBOARD_SECRET_KEY', 'spotbot_secret_key_change_me')
}

DB_CONFIG = {
    'url': clean_env('DATABASE_URL', 'sqlite:///spotbot.db')
}
DATABASE_URL = DB_CONFIG['url']

TOP_40_SYMBOLS = [
    'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'LINKUSDT',
    'DOTUSDT', 'POLUSDT', 'LTCUSDT', 'UNIUSDT',
    'APTUSDT', 'SUIUSDT', 'FETUSDT', 'RENDERUSDT',
    'FILUSDT', 'ARBUSDT', 'OPUSDT', 'INJUSDT', 'STXUSDT',
    'TIAUSDT', 'GRTUSDT', 'AAVEUSDT', 'FTMUSDT', 'ICPUSDT'
]

TOP_10_FUTURES_SYMBOLS = [
    'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'LINKUSDT'
]

SCANNER_CONFIG = {
    'enabled': True,
    'top_symbols': TOP_40_SYMBOLS,
    'min_order_usdt': 10.0,
    'macro_interval': '4h',
    'default_micro_interval': '1h',
    'scalping_micro_interval': '15m',
    'adaptive_interval': True,
    'max_concurrent_positions': 3,
    'reserve_fraction_for_dca': 0.25
}

MAX_CONCURRENT_POSITIONS = SCANNER_CONFIG['max_concurrent_positions']
RESERVE_FRACTION_FOR_DCA = SCANNER_CONFIG['reserve_fraction_for_dca']

TRADING_CONFIG = {
    'symbol': 'BTCUSDT',
    'interval': '4h',
    'limit': 300,
    'depth': 20,
    'maxlen': 10,
    'period': 14,
    'num_std': 2.0,
    'volume_avg': 50,
    'adx_period': 14,
    'min_adx': 20.0,
    'sell_pressure_threshold': 0.65,
    'macd_fast': 12,
    'macd_slow': 26,
    'macd_signal': 9,
    'use_ema_filter': True,
    'use_candle_close_confirmation': True
}

RISK_PROFILES = {
    'Conservador': {
        'rsi_threshold': 22,
        'adx_min': 25.0,
        'risk_percent': 1.0
    },
    'Moderado': {
        'rsi_threshold': 30,
        'adx_min': 20.0,
        'risk_percent': 2.0
    },
    'Agressivo': {
        'rsi_threshold': 35,
        'adx_min': 15.0,
        'risk_percent': 3.0
    }
}

ACTIVE_RISK_PROFILE = 'Moderado'
PAPER_TRADING_MODE = False # Ativa o uso da Binance Mainnet
PAPER_TRADING = False
STATS_BASELINE_ID = 0 # Captura todos os novos trades do banco limpo na Mainnet

RSI_CONFIG = {
    'levels': [25, 23, 20, 18, 15, 12],
    'dynamic_low': [25, 23, 20, 18, 15, 12],
    'min': [15, 15, 15, 15, 15, 15],
    'high': 70
}

ATR_CONFIG = {
    'period': 14,
    'tp_multiplier': 2.0,
    'sl_multiplier': 1.5,
    'use_atr_stop': True
}

OCO_CONFIG = {
    'target_profit_percent': 0.025,
    'stop_loss_percent': 0.02,
    'stop_limit_buffer': 0.005
}

OCO_ORDER_CONFIG = {
    'lucro_alvo_percent': 2.0,
    'stop_loss_percent': 2.0,
}

TRAILING_STOP_CONFIG = {
    'enabled': True,
    'activation_percent': 0.015,
    'callback_percent': 0.008,
}
