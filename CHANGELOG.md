# Changelog
Todos os registros de evolução da arquitetura do **SpotBot Pro** estão listados aqui.

## [v7.0.1] - Dynamic Risk & Elite Asset Selection
### Added
- **Dynamic Risk Sizing Escalonado (`futures_engine.py`):** Sistema adaptativo de risco por trade com base no saldo total (8% para bancas $\le \$200$, 5% até $\$1.000$, 3% até $\$3.000$ e 2% institucional acima de $\$3.000$), garantindo a ultrapassagem de valores nocionais mínimos da Binance.
- **Dynamic Leverage Engine (até 50x):** Cálculo automático de alavancagem $\frac{1}{\text{Stop Loss \%} \times 2.0}$ para acomodar ordens de scalping com stops curtos em 15m.
- **Stop Preventivo Antecipado (-9.0% ROI):** Fechamento a mercado automático ao atingir -9% de ROI para estancar perdas antes do Stop Loss rígido.
- **Take-Profit Máximo (+5.5% ROI):** Fechamento dinâmico instantâneo na máxima lucratividade de scalping.
- **Cesta Top 6 Elite:** Refinamento dos pares operados no Futuros e Scanner (`BTC`, `ETH`, `SOL`, `BNB`, `XRP`, `LINK`), eliminando pares com ruído técnico excessivo.
- **Fix de UDS / Telegram Trade Closure:** Preservação de estado na memória no Trailing Lock para captura e cálculo de PnL em tempo real via WebSocket de ordens executadas.

## [v7.0] - HedgeFund & Futures Market Edition
### Added
- **Mercado de Futuros Integrado (20x Isolated Leverage):** Motor de trading paralelo (`core/futures_engine.py`) operando simultaneamente com o Mercado Spot.
- **15m Bollinger Band Sniper Engine:** Motor especialista em reversão à média extrema calculando bandas de Bollinger e RSI no gráfico de 15m.
- **Futures Liquidation Buffer Guard (`futures_risk_manager.py`):** Sistema automatizado de proteção que valida e ajusta a alavancagem garantindo margem de segurança de no mínimo 15% entre o Stop Loss e o Preço de Liquidação.
- **Futures Order Flow CVD & Lead-Lag Alpha (1m):** Algoritmo de fita de agressão e rastreamento de microsurtos (0.3% BTC / 0.4% ETH em 1m).
- **Dashboard Web & Telegram Bot Dual Mode:** Telas, gráficos e submenus adaptados para visualização unificada de posições Spot (OCO) e Futuros.
- **Suporte Híbrido SQLite / PostgreSQL 24/7:** Execução local em SQLite e em nuvem via PostgreSQL (injetado via `DATABASE_URL` no Railway).

---

## [v6.0]
### Added
- **Trailing Profit Lock (Market Sell):** Nova trava de lucros ao atingir 75% da meta TP, garantindo o fechamento imediato via Market Sell na menor queda, sem risco de expiração da OCO.
- **Sincronização BRT (America/Sao_Paulo):** Padronização integral de todo o relógio lógico da aplicação, logs, banco de dados e relatórios diários em PDF para o horário de Brasília, evitando falhas de virada do dia via horário UTC no Railway.
- **DCA PnL Override:** Lógica que corrige o cálculo de PnL Bruto para englobar de forma correta o capital inserido durante repiques (Smart Recovery DCA).

### Changed
- **Metas Conservadoras (TP/SL):** Parametrização ajustada para TP alvo de 2.0% a 3.0% e SL protetivo de 1.5% a 2.0% garantindo a eficiência de capital a curto prazo.
- Correção no dashboard para re-renderizar baseando-se no maior ID do banco SQLite em vez da quantidade bruta de registros, destravando a contagem.

---

## [v5.0]
### Added
- **Smart Recovery DCA & Flash Dump Protection:** Motor de recompra dinâmica em retrações em níveis institucionais de Suporte Fibonacci (61.8%).
- **Correlation Lead-Lag Alpha Engine:** Algoritmo que rastreia os surtos do BTC (>= 0.25% em 3m) para disparar antecipações nas Altcoins do Top 40.
- **Order Flow CVD Tape Reading:** Leitura contínua dos últimos 500 ticks de mercado, verificando desbalanceamento de `maker` vs `taker` buys >= 60%.
- **Kelly Criterion Position Sizing:** Gestão de lotes com modelo "Half-Kelly" utilizando as taxas de vitória históricas extraídas do SQLite.
- **Cointegration Pair Trading:** Reversão à média através do Z-Score em comparação ao Bitcoin.
