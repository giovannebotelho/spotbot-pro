# 📖 DOCUMENTAÇÃO TÉCNICA DE ARQUITETURA E ENGENHARIA DE SOFTWARE
## SPOTBOT PRO v7.0.1-HEDGE_FUND — SISTEMA INSTITUCIONAL DE TRADING ALGORÍTMICO QUANTITATIVO E INTELIGÊNCIA ARTIFICIAL

---

### 📋 SUMÁRIO EXECUTIVO
- **Nome do Sistema**: SpotBot Pro (HedgeFund & Futures Quantitative Engine)
- **Versão de Software**: `v7.0.1-HEDGE_FUND`
- **Classificação de Confiabilidade**: *Critical Fault-Tolerant System (Medical-Grade Standard ISO/IEC 25010)*
- **Arquitetura**: Multi-threaded Async I/O (Python `asyncio`), Event-Driven WebSockets & Neural AI Synthesis
- **Exchange Suportada**: Binance Spot & Binance Futures (API REST v3/v1 & WebSocket User/Market Streams)
- **Cesta de Ativos Operados (Top 6 Elite)**: `['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'LINKUSDT']`
- **Modelos Quantitativos Integrados**:
  - 15m Bollinger Band Sniper Engine (Mean Reversion em 15m)
  - Dynamic Leverage Engine (Alavancagem Dinâmica Isolada até 50x)
  - Dynamic Risk Sizing Escalonado (8%, 5%, 3%, 2% conforme saldo da banca)
  - Trailing Lock Dinâmico & Stop Preventivo Antecipado (-9.0% ROI)
  - Take-Profit Máximo Automático (+5.5% ROI)
  - Futures Liquidation Buffer Guard (`futures_risk_manager.py`)
  - Order Flow Cumulative Volume Delta (CVD Tape Reading em 500 Trades)
  - Correlation Lead-Lag Alpha Engine (BTC 1m Momentum Lead)
  - Smart Recovery DCA em Suportes de Fibonacci (61.8% e 78.6%)
  - AI Sentiment & Market Panic Scanner (CryptoPanic + Gemini 2.5 Flash)
  - Multi-Timeframe Confluence Matrix (4H + 1H + 15M $\ge 70\%$)
  - Orderbook 50 Depth & Whale Wall Protection
  - Dynamic ATR Volatility SL/TP Protection

---

# CAPÍTULO 1: FILOSOFIA QUANTITATIVA E CASCATA DE VALIDAÇÃO

O **SpotBot Pro v7.0.1** opera sob o conceito de **Filtros Quantitativos Hierárquicos** (Cascata de Validação em Tempo Real em Spot e Futuros):

```text
[Dados de Mercado em Tempo Real (WebSockets & REST Spot/Futures)]
                       │
                       ▼
  [Camada 0: AI Panic News Scanner (CryptoPanic + Gemini IA)]
                       │ (Trava compras se Sentiment Score < 30)
                       ▼
  [Camada 1: Matriz Multi-Timeframe (4H + 1H + 15M) & Regime Shield]
                       │ (Exige Confluência e bloqueia contra a tendência macro do BTC)
                       ▼
  [Camada 2: Lead-Lag Alpha Engine (BTC 1m Lead Momentum)]
                       │ (Antecipa impulso de volume do BTC em altcoins da Elite)
                       ▼
  [Camada 3: Order Flow CVD Tape Reading (500 Market Trades)]
                       │ (Confirma agressão compradora/vendedora)
                       ▼
  [Camada 4: Candle Shield & Anti-Faca Caindo]
                       │ (Bloqueia entradas em velas de rejeição ou exaustão violenta)
                       ▼
  [Camada 5: Sniper Mode Observation]
                       │ (Aguarda até 3 minutos pela exaustão para obter o melhor preço)
                       ▼
  [Camada 6: Dynamic Risk Sizing & Dynamic Leverage Engine]
                       │ (Calcula risco em $ e alavancagem até 50x sob medida)
                       ▼
  [Camada 7: Liquidation Risk-Buffer Guard]
                       │ (Garante buffer de segurança > 1.5x entre SL e Preço de Liquidação)
                       ▼
  [Camada 8: Ordem a Mercado + OCO/Condicionais + Trailing Lock Monitor 1s]
```

---

# CAPÍTULO 2: GESTÃO DE RISCO E DIMENSIONAMENTO DE CAPITAL

### 1. Dimensionamento Dinâmico por Risco Escalonado
Para assegurar a operacionalidade tanto de contas institucionais quanto de pequenas contas de teste, o risco percentual é ajustado de forma inversamente proporcional à magnitude da banca:

| Saldo Total (USDT) | Risco por Trade (%) | Finalidade Estratégica |
| :--- | :--- | :--- |
| $\le \$200$ | **8.0%** | Superação de regras de valor nocional mínimo da exchange |
| $\$201$ a $\$1.000$ | **5.0%** | Fase de alavancagem de crescimento inicial |
| $\$1.001$ a $\$3.000$ | **3.0%** | Perfil equilibrado de transição |
| $> \$3.000$ | **2.0%** | Gestão institucional padrão Hedge Fund |

### 2. Dimensionamento da Posição (Notional)
$$\text{Notional (USDT)} = \left(\frac{\text{Risco Aceito (USDT)}}{|\text{Preço de Entrada} - \text{Stop Loss}|}\right) \times \text{Preço de Entrada}$$

* **Hard Cap de Exposição:** A exposição nocional máxima permitida por operação é limitada a $10\times$ o saldo total da conta.

---

# CAPÍTULO 3: MOTOR DE ALAVANCAGEM DINÂMICA (ISOLATED)

A alavancagem não é parametrizada estaticamente; ela é determinada pela distância percentual do Stop Loss:

$$\text{Alavancagem} = \min\left(50, \max\left(1, \frac{1}{\text{Distância SL \%} \times 2.0}\right)\right)$$

* **Margem Isolada:** Todo trade de futuros é executado obrigatoriamente no modo `ISOLATED`, isolando o risco estritamente à margem alocada no trade.

---

# CAPÍTULO 4: MOTOR DE PROTEÇÃO EM TEMPO REAL (TRAILING LOCK)

Executado continuamente com latência de 1 segundo sobre o retorno sobre capital próprio ($\text{ROI}$):

1. **Take-Profit Máximo:** Fechamento a mercado imediato ao atingir $\text{ROI} \ge +5.50\%$.
2. **Trailing Lock Dinâmico:** Se $\text{Peak ROI} \ge +3.00\%$ e o recuo do pico for $\ge 3.00\%$, liquida a mercado garantindo saída com lucro/breakeven.
3. **Stop Preventivo Antecipado:** Se $\text{ROI} \le -9.00\%$, fecha a mercado antes do Stop Loss rígido, reduzindo a perda pela metade.
