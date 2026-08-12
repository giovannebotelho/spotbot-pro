import asyncio
import aiohttp
from config.settings import TRADING_CONFIG

async def get_usdt_balance(client):
    """Obtém o saldo disponível de USDT na conta do cliente."""
    balance = await client.get_asset_balance(asset='USDT')
    return float(balance['free'])

async def get_order_book(client, symbol, depth=None):
    """Recupera o livro de ofertas (order book) para um símbolo com a profundidade solicitada."""
    if depth is None:
        depth = TRADING_CONFIG['depth']
    order_book = await client.get_order_book(symbol=symbol, limit=depth)
    return order_book

async def get_order_details(client, symbol, order_id):
    """Obtém detalhes de uma ordem específica pelo ID."""
    order_details = await client.get_order(symbol=symbol, orderId=order_id)
    return order_details

def extract_closes(klines):
    """Extrai os preços de fechamento de uma lista de velas."""
    return [float(kline[4]) for kline in klines]

def extract_volumes(klines):
    """Extrai os volumes de uma lista de velas."""
    return [float(kline[5]) for kline in klines]

async def get_klines(client, symbol, interval, limit):
    """Obtém as velas (klines) para um símbolo específico."""
    klines = await client.get_klines(symbol=symbol, interval=interval, limit=limit)
    return klines

async def get_multi_klines(client, symbols, interval, limit):
    """Obtém klines em lote de forma assíncrona para múltiplos símbolos."""
    async def fetch(sym):
        try:
            res = await client.get_klines(symbol=sym, interval=interval, limit=limit)
            return sym, res
        except Exception:
            return sym, []

    tasks = [fetch(s) for s in symbols]
    results = await asyncio.gather(*tasks)
    return dict(results)

async def get_bnb_price(client):
    """Obtém o preço atual do BNB em USDT."""
    ticker = await client.get_symbol_ticker(symbol="BNBUSDT")
    return float(ticker['price'])

async def get_futures_analytics(symbol):
    """
    Obtém métricas de Derivativos (Futures Funding Rate & Open Interest) da Binance em tempo real.
    Permite detectar potenciais setups de Short Squeeze quando o Funding Rate está significativamente negativo.
    """
    url_funding = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}"
    url_oi = f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}"
    
    funding_rate = 0.0
    open_interest = 0.0
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url_funding, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    funding_rate = float(data.get('lastFundingRate', 0.0))

            async with session.get(url_oi, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    open_interest = float(data.get('openInterest', 0.0))
    except Exception as e:
        pass

    return {
        'symbol': symbol,
        'funding_rate': funding_rate,
        'funding_rate_pct': funding_rate * 100.0,
        'open_interest': open_interest,
        'is_short_heavy': funding_rate < -0.0001 # Funding Rate < -0.01%
    }

async def get_multi_timeframe_klines(client, symbol):
    """
    Obtém em paralelo ultra-rápido as klines de 3 horizontes de tempo (4h, 1h, 15m)
    para o cálculo da Matriz de Confluência Multi-Timeframe (v4.0).
    """
    async def fetch(tf, limit):
        try:
            return await client.get_klines(symbol=symbol, interval=tf, limit=limit)
        except Exception:
            return []

    res_4h, res_1h, res_15m = await asyncio.gather(
        fetch('4h', 100),
        fetch('1h', 100),
        fetch('15m', 100)
    )
    return {
        '4h': res_4h,
        '1h': res_1h,
        '15m': res_15m
    }

async def get_lead_lag_btc_klines(client):
    """
    FASE 2 (v5.0): Obtém as últimas velas de 1m do BTCUSDT em tempo real
    para o cálculo do Motor de Antecipação (Lead-Lag Alpha Engine).
    """
    try:
        return await client.get_klines(symbol="BTCUSDT", interval="1m", limit=15)
    except Exception:
        return []

async def get_recent_trades_cvd(client, symbol, limit=500):
    """
    FASE 3 (v5.0): Obtém as últimas 500 negociações a mercado para cálculo do
    Cumulative Volume Delta (CVD Tape Reading Engine).
    """
    try:
        return await client.get_recent_trades(symbol=symbol, limit=limit)
    except Exception:
        return []

# ---------------------------------------------------------
# FUTURES MARKET WRAPPERS (HedgeFund Edition)
# ---------------------------------------------------------

async def get_futures_usdt_balance(client):
    """Obtém o saldo disponível de USDT na conta de Futuros (USDS-M)."""
    try:
        if hasattr(client, 'session') and client.session:
            client.session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json",
            })
            
        balances = await client.futures_account_balance()
        for asset in balances:
            if asset['asset'] == 'USDT':
                # No /fapi/v2/balance a chave correta para o saldo sacável/disponível é 'availableBalance' ou 'maxWithdrawAmount'
                return float(asset.get('availableBalance', asset.get('withdrawAvailable', asset.get('balance', 0.0))))
        return 0.0
    except Exception as e:
        if '403 Forbidden' in str(e) or 'CloudFront' in str(e) or 'APIError(code=0)' in str(e):
            print(f"⚠️ Aviso WAF/CloudFront ao buscar saldo Futuros: {e}. Retornando 0 para proteger o loop.")
        else:
            print(f"⚠️ Erro ao buscar saldo USDT Futuros: {e}")
        return 0.0

async def get_futures_whale_ratio(client, symbol, period='15m'):
    """
    Busca o 'Top Trader Long/Short Ratio' (Positions) da Binance Futures.
    Retorna float: > 1 significa baleias em LONG, < 1 significa baleias em SHORT.
    """
    try:
        data = await client.futures_top_longshort_position_ratio(symbol=symbol, period=period)
        if data and len(data) > 0:
            return float(data[-1]['longShortRatio'])
    except Exception as e:
        print(f"⚠️ Erro ao buscar Whale Ratio de {symbol}: {e}")
    return 1.0  # Neutro em caso de erro

async def setup_futures_margin(client, symbol, leverage=15, margin_type='ISOLATED'):
    """
    Configura a alavancagem e o tipo de margem para o ativo.
    margin_type: 'ISOLATED' ou 'CROSSED'
    """
    try:
        # Tenta mudar a margem
        await client.futures_change_margin_type(symbol=symbol, marginType=margin_type)
    except Exception as e:
        err_str = str(e)
        if "-4046" not in err_str and "-4067" not in err_str and "-4131" not in err_str:
            if "-1121" in err_str or "-4141" in err_str or "-4028" in err_str:
                return False
            print(f"Warning setting margin type for {symbol}: {e}")
            
    try:
        # Configura alavancagem
        await client.futures_change_leverage(symbol=symbol, leverage=leverage)
    except Exception as e:
        err_str = str(e)
        if "-1121" in err_str or "-4141" in err_str or "-4028" in err_str:
            return False
        print(f"Warning setting leverage for {symbol}: {e}")
        
    return True

async def place_futures_order(client, symbol, side, order_type, quantity, price=None, reduce_only=False):
    """
    Envia uma ordem para o mercado de futuros.
    side: 'BUY' ou 'SELL'
    order_type: 'MARKET', 'LIMIT', 'STOP_MARKET', 'TAKE_PROFIT_MARKET', etc.
    """
    params = {
        'symbol': symbol,
        'side': side,
        'type': order_type,
        'quantity': quantity
    }
    if price and order_type != 'MARKET':
        params['price'] = str(price)
        params['timeInForce'] = 'GTC'
        
    if reduce_only:
        params['reduceOnly'] = 'true'
        
    return await client.futures_create_order(**params)

async def place_futures_conditional_order(client, symbol, side, order_type, quantity, stop_price=None, callback_rate=None):
    """
    Envia uma ordem condicional para Futuros (STOP_MARKET, TAKE_PROFIT_MARKET ou TRAILING_STOP_MARKET).
    Essas ordens são usadas como Stop Loss e Take Profit no Futures.
    """
    params = {
        'symbol': symbol,
        'side': side,
        'type': order_type,
        'quantity': quantity,
        'reduceOnly': 'true'
    }
    
    if order_type in ['STOP_MARKET', 'TAKE_PROFIT_MARKET'] and stop_price:
        params['stopPrice'] = str(stop_price)
        params['timeInForce'] = 'GTC'
        
    if order_type == 'TRAILING_STOP_MARKET':
        if callback_rate:
            params['callbackRate'] = str(callback_rate)
        if stop_price:
            params['activationPrice'] = str(stop_price)
            
    return await client.futures_create_order(**params)

async def get_futures_order_details(client, symbol, order_id):
    """Obtém detalhes de uma ordem específica de Futuros pelo ID (tenta order regular e depois algo order)."""
    try:
        return await client.futures_get_order(symbol=symbol, orderId=order_id)
    except Exception as e:
        if "does not exist" in str(e) or "-2013" in str(e):
            algo_res = await client.futures_get_algo_order(algoId=order_id)
            if isinstance(algo_res, list) and len(algo_res) > 0:
                res = algo_res[0]
            elif isinstance(algo_res, dict):
                res = algo_res
            
            if res:
                if 'algoStatus' in res:
                    if res['algoStatus'] in ['WORKING', 'NEW']:
                        res['status'] = 'NEW'
                    elif res['algoStatus'] == 'CANCELLED':
                        res['status'] = 'CANCELED'
                    elif res['algoStatus'] == 'EXECUTED':
                        res['status'] = 'FILLED'
                    else:
                        res['status'] = res['algoStatus']
                return res
        raise e

async def cancel_futures_order(client, symbol, order_id):
    """Cancela uma ordem de futuros, tentando regular e depois algo."""
    try:
        return await client.futures_cancel_order(symbol=symbol, orderId=order_id)
    except Exception as e:
        if "Unknown order" in str(e) or "-2011" in str(e):
            return await client.futures_cancel_algo_order(symbol=symbol, algoId=order_id)
        raise e

async def get_futures_klines(client, symbol, interval, limit):
    """Obtém as velas (klines) para um símbolo de Futuros específicos."""
    return await client.futures_klines(symbol=symbol, interval=interval, limit=limit)

