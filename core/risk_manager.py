import asyncio
import time
from services.gemini_ai import auto_tune_risk_profile
from services.telegram_notifier import send_telegram_message

async def check_daily_circuit_breaker(db, usdt_balance, current_dt, globals_dict, telegram_config, log_callback=print, status_callback=print):
    """
    FASE 2: Avalia o Daily Circuit Breaker (-20.0% Max Drawdown Diário).
    Retorna True se o Circuit Breaker foi ativado (e a rotina principal deve aguardar).
    """
    daily_stats = db.get_daily_stats()
    daily_pnl = daily_stats['daily_pnl']
    circuit_breaker_limit = -abs(max(20.0, usdt_balance * 0.20))
    
    if daily_pnl <= circuit_breaker_limit:
        status_callback(f"🚨 DAILY CIRCUIT BREAKER ATIVADO ({daily_pnl:+.2f} USDT). Novas compras pausadas por 12h...")
        if telegram_config.get('bot_token') and telegram_config.get('chat_id') and globals_dict.get('_last_cb_alert') != current_dt.date():
            globals_dict['_last_cb_alert'] = current_dt.date()
            asyncio.create_task(send_telegram_message(
                telegram_config['bot_token'], telegram_config['chat_id'],
                f"🚨 <b>DAILY CIRCUIT BREAKER ATIVADO!</b>\n\n"
                f"📊 Perda acumulada hoje de <b>${daily_pnl:.2f} USDT</b> atingiu o limite de proteção de -20.0%!\n"
                f"🛡️ Novas compras pausadas por 12 horas enquanto as ordens OCO ativas continuam sendo monitoradas."
            ))
        await asyncio.sleep(600)
        return True
    return False

async def check_gemini_auto_tuning(db, globals_dict, telegram_config, log_callback=print):
    """
    FASE 3: Avalia as condições de mercado via IA Gemini a cada 120 min e sugere o perfil de risco ideal.
    """
    current_timestamp = time.time()
    if current_timestamp - globals_dict.get('_last_autotune_time', 0) >= 7200:
        globals_dict['_last_autotune_time'] = current_timestamp
        try:
            db_stats = db.get_stats()
            acc_pnl = db_stats['total_net_profit']
            rec_profile, rec_just = auto_tune_risk_profile("ALTA", db_stats['win_rate'], acc_pnl)
            
            from config.settings import RISK_PROFILES
            import config.settings as setts
            if rec_profile in RISK_PROFILES and setts.ACTIVE_RISK_PROFILE != rec_profile:
                setts.ACTIVE_RISK_PROFILE = rec_profile
                log_callback(f"🧠 \033[1;36mGemini Auto-Tuning\033[0m: Perfil de Risco ajustado para \033[1;32m{rec_profile}\033[0m! ({rec_just})")
                if telegram_config.get('bot_token') and telegram_config.get('chat_id'):
                    asyncio.create_task(send_telegram_message(
                        telegram_config['bot_token'], telegram_config['chat_id'],
                        f"🧠 <b>GEMINI AUTO-TUNING DE RISCO</b>\n\n"
                        f"🎯 Novo Perfil Recomendado: <b>{rec_profile}</b>\n"
                        f"📝 Justificativa IA: <i>{rec_just}</i>"
                    ))
        except Exception as at_err:
            log_callback(f"⚠️ Aviso no Gemini Auto-Tuning: {at_err}")
