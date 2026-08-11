import json
import os
import google.generativeai as genai
from config.settings import API_KEYS
from services.news_scanner import fetch_crypto_news

import time

_news_cache = {}

async def evaluate_news_sentiment(symbol, log=print):
    """
    Avalia o sentimento das notícias recentes usando Gemini Flash.
    Retorna: (score_0_100, direction, justification)
    """
    global _news_cache
    now = time.time()
    if symbol in _news_cache and now - _news_cache[symbol]['timestamp'] < 300: # 5 minutos de cache
        return _news_cache[symbol]['score'], _news_cache[symbol]['direction'], _news_cache[symbol]['reason']
        
    try:
        headlines = await fetch_crypto_news(symbol)
        if not headlines:
            return 50, None, "Sem notícias recentes."
            
        api_key = API_KEYS.get('gemini') or os.getenv('GEMINI_API_KEY')
        if not api_key:
            return 50, None, "API Key do Gemini não configurada."
            
        genai.configure(api_key=api_key)
        
        prompt = f"""
        Você é um analista Quant de Hedge Fund de Alta Frequência. 
        Analise estas notícias recentes para o ativo cripto {symbol}.
        Responda APENAS com um JSON estrito no seguinte formato:
        {{"score": int (0 a 100), "direction": "LONG", "SHORT" ou "NEUTRAL", "reason": "string curta"}}
        
        Regras de Score:
        - <= 25: Extreme Panic / Bearish (Crash iminente, hacks, regulação severa) -> SHORT
        - >= 80: Extreme Euphoria / Bullish (Parcerias gigantes, listagem, adoção) -> LONG
        - 26 a 79: Neutro/Misto -> NEUTRAL
        
        Notícias:
        """ + "\n".join(f"- {h}" for h in headlines)
        
        model = genai.GenerativeModel('gemini-2.5-flash', generation_config={"response_mime_type": "application/json"})
        response = model.generate_content(prompt)
        
        try:
            res_data = json.loads(response.text)
            score = int(res_data.get('score', 50))
            direction = res_data.get('direction', 'NEUTRAL')
            
            if direction not in ['LONG', 'SHORT']:
                direction = None
                
            _news_cache[symbol] = {'score': score, 'direction': direction, 'reason': res_data.get('reason', ''), 'timestamp': now}
            return score, direction, res_data.get('reason', '')
        except json.JSONDecodeError:
            log("⚠️ Erro ao decodificar JSON do Gemini para análise de notícias.")
            return 50, None, "Erro de parse"
            
    except Exception as e:
        log(f"⚠️ Erro ao avaliar sentimento de notícias para {symbol}: {e}")
        return 50, None, str(e)
