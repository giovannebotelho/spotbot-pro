import os
import sys
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable
)
from reportlab.pdfgen import canvas

class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont("Helvetica-Bold", 8)
        self.setFillColor(colors.HexColor("#64748B"))
        
        # Cabeçalho (Páginas > 1)
        if self._pageNumber > 1:
            self.drawString(40, 805, "SPOTBOT PRO v7.0.1 (HEDGEFUND EDITION) — DOSSIÊ TÉCNICO & OPERACIONAL")
            self.drawRightString(555, 805, "DOCUMENTAÇÃO OFICIAL")
            self.setStrokeColor(colors.HexColor("#CBD5E1"))
            self.setLineWidth(0.5)
            self.line(40, 798, 555, 798)

        # Rodapé
        self.setStrokeColor(colors.HexColor("#CBD5E1"))
        self.setLineWidth(0.5)
        self.line(40, 45, 555, 45)

        self.setFont("Helvetica", 8)
        self.drawString(40, 32, "SpotBot Pro v7.0.1 • Algoritmo Quantitativo Institucional e IA Generativa")
        page_str = f"Página {self._pageNumber} de {page_count}"
        self.drawRightString(555, 32, page_str)
        self.restoreState()

def build_pdf(output_filename="docs/SpotBot_Pro_Dossie_Tecnico_v7.pdf"):
    os.makedirs(os.path.dirname(output_filename), exist_ok=True)
    
    doc = SimpleDocTemplate(
        output_filename,
        pagesize=A4,
        leftMargin=40, rightMargin=40,
        topMargin=50, bottomMargin=50
    )

    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=20,
        leading=24,
        textColor=colors.HexColor('#0F172A'),
        spaceAfter=6
    )
    
    subtitle_style = ParagraphStyle(
        'DocSubTitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#059669'),
        spaceAfter=15
    )

    h1_style = ParagraphStyle(
        'H1',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=12,
        leading=16,
        textColor=colors.HexColor('#0F172A'),
        spaceBefore=12,
        spaceAfter=6
    )

    body_style = ParagraphStyle(
        'Body',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=13,
        textColor=colors.HexColor('#334155'),
        spaceAfter=6
    )

    bullet_style = ParagraphStyle(
        'Bullet',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor('#334155'),
        leftIndent=12,
        spaceAfter=3
    )

    table_header_style = ParagraphStyle(
        'TH',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=11,
        textColor=colors.white,
        alignment=1
    )

    table_cell_style = ParagraphStyle(
        'TC',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=10,
        textColor=colors.HexColor('#1E293B'),
        alignment=1
    )

    story = []

    # Header
    story.append(Paragraph("SPOTBOT PRO v7.0.1 — DOSSIÊ TÉCNICO", title_style))
    story.append(Paragraph("Manual de Engenharia Quantitativa, Parâmetros e Gestão de Risco • HedgeFund Edition", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#059669'), spaceBefore=0, spaceAfter=12))

    # Seção 1
    story.append(Paragraph("1. 🪙 Cestas de Ativos Selecionados (Elite List)", h1_style))
    story.append(Paragraph("O sistema opera com uma seleção enxuta e rigorosamente filtrada por liquidez e respeito técnico:", body_style))
    story.append(Paragraph("• <b>Mercado de Futuros (USDT-M):</b> BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, LINKUSDT (Top 6 Elite).", bullet_style))
    story.append(Paragraph("• <b>Mercado Spot (Scanner OCO):</b> Ativos consolidados com book institucional profundo (sem memecoins ou ativos ilíquidos).", bullet_style))
    story.append(Paragraph("• <b>Timeframes de Análise:</b> Macro em 1H e 4H (Regime e EMA200); Micro em 15M (Price Action, Bandas de Bollinger e CVD).", bullet_style))
    story.append(Spacer(1, 8))

    # Seção 2
    story.append(Paragraph("2. 💰 Gestão de Banca & Risco Escalonado (Dynamic Risk Sizing)", h1_style))
    story.append(Paragraph("O risco por operação é ajustado proporcionalmente ao tamanho da banca para garantir flexibilidade e contornar nocionais mínimos:", body_style))

    table_data = [
        [Paragraph("Tamanho da Banca (USDT)", table_header_style), Paragraph("Risco Máximo por Trade (%)", table_header_style), Paragraph("Perfil Estratégico", table_header_style)],
        [Paragraph("Até $200", table_cell_style), Paragraph("<b>8.0%</b>", table_cell_style), Paragraph("Superação de Nocional Mínimo", table_cell_style)],
        [Paragraph("$201 a $1.000", table_cell_style), Paragraph("<b>5.0%</b>", table_cell_style), Paragraph("Crescimento Acelerado", table_cell_style)],
        [Paragraph("$1.001 a $3.000", table_cell_style), Paragraph("<b>3.0%</b>", table_cell_style), Paragraph("Equilibrado / Moderado", table_cell_style)],
        [Paragraph("Acima de $3.000", table_cell_style), Paragraph("<b>2.0%</b>", table_cell_style), Paragraph("Gestão Institucional Conservadora", table_cell_style)],
    ]
    t = Table(table_data, colWidths=[150, 160, 205])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0F172A')),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor('#F8FAFC'), colors.white]),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 6))
    story.append(Paragraph("• <b>Fórmula do Nocional:</b> <code>Notional = (Risco_Aceito_em_$ / Distancia_StopLoss_em_$) * Preco_Atual</code>", bullet_style))
    story.append(Paragraph("• <b>Hard Cap de Exposição:</b> Posição nocional máxima limitada a 10x a banca total por operação.", bullet_style))
    story.append(Spacer(1, 8))

    # Seção 3
    story.append(Paragraph("3. ⚙️ Alavancagem Dinâmica (Dynamic Leverage Engine)", h1_style))
    story.append(Paragraph("A alavancagem é calculada em tempo real para acomodar o tamanho da ordem sem ultrapassar o risco:", body_style))
    story.append(Paragraph("• <b>Modo de Margem:</b> <code>ISOLATED</code> obrigatório em todas as ordens de futuros.", bullet_style))
    story.append(Paragraph("• <b>Fórmula de Cálculo:</b> <code>Alavancagem = 1.0 / (Stop_Loss_% * 2.0)</code> (limitada dinamicamente entre 1x e 50x).", bullet_style))
    story.append(Paragraph("• <b>Liquidation Buffer:</b> O motor de risco assegura que a liquidação estimada fique pelo menos 1.5x além do Stop Loss.", bullet_style))
    story.append(Spacer(1, 8))

    # Seção 4
    story.append(Paragraph("4. 🎯 Take Profit e Stop Loss por Estratégia", h1_style))
    story.append(Paragraph("• <b>Entradas Padrão (Price Action / Indicadores / CVD):</b> Stop Loss em <code>2.5x ATR(14)</code> | Take Profit em <code>3.0x ATR(14)</code>.", bullet_style))
    story.append(Paragraph("• <b>Band-Sniper 15M (Reversão à Média):</b> Stop na Banda oposta / fundo recente | Take Profit na SMA 20 (máx 0.40% de deslocamento).", bullet_style))
    story.append(Paragraph("• <b>Gemini AI / News Alpha:</b> Stop Loss em <code>0.3x ATR(14)</code> | Take Profit em <code>0.5x ATR(14)</code>.", bullet_style))
    story.append(Spacer(1, 8))

    # Seção 5
    story.append(Paragraph("5. 🛡️ Motor de Defesa em Tempo Real (Trailing Lock, Parcial 50% & Breakeven)", h1_style))
    story.append(Paragraph("O monitor de liquidação roda a cada 1 segundo sobre o Retorno sobre Capital Próprio (ROI):", body_style))
    story.append(Paragraph("0. <b>Parcial Dinâmica (50% do lote em +3.50% ROI):</b> Realiza 50% da posição a mercado no primeiro alvo e move o Stop Loss do restante para o preço de entrada (Breakeven - Risco Zero).", bullet_style))
    story.append(Paragraph("1. <b>Take-Profit Máximo Adaptativo por ATR:</b> Encerramento da posição restante na máxima expansão de volatilidade (4.5% a 12.0% ROI).", bullet_style))
    story.append(Paragraph("2. <b>Trailing Lock Dinâmico:</b> Ao atingir pico de ROI >= +3.00%, um recuo de 3.00% aciona saída a mercado no positivo.", bullet_style))
    story.append(Paragraph("3. <b>Stop Preventivo Adaptativo por ATR:</b> Encerramento antecipado a mercado (-7.5% a -14.0% ROI) antes do SL rígido, mitigando o drawdown.", bullet_style))
    story.append(Paragraph("4. <b>Circuit Breaker Diário (-5.0% Max Drawdown):</b> Se o saldo total diário recuar 5.0%, pausa novas entradas por 6 horas e alerta no Telegram.", bullet_style))
    story.append(Spacer(1, 8))

    # Seção 6
    story.append(Paragraph("6. 🧠 Escudos e Filtros Anti-Violinada Ativos", h1_style))
    story.append(Paragraph("• <b>Regime Shield:</b> Alinhamento obrigatório com a tendência macro do BTC no gráfico de 1H.", bullet_style))
    story.append(Paragraph("• <b>Multi-Timeframe (MTF):</b> Operações de continuidade exigem validação da EMA20 no gráfico de 1H.", bullet_style))
    story.append(Paragraph("• <b>Squeeze Detector (Bollinger BandWidth):</b> Bloqueia entradas em mercado lateral comprimido (< 0.8% largura) sem volume de rompimento.", bullet_style))
    story.append(Paragraph("• <b>Orderbook Imbalance (Futuros Depth 20):</b> Bloqueia compras se houver pressão vendedora no book e vice-versa.", bullet_style))
    story.append(Paragraph("• <b>Tape Reading / CVD:</b> Filtro de fluxo que aborta entradas contra a agressão compradora/vendedora do livro.", bullet_style))
    story.append(Paragraph("• <b>Candle Shield & Anti-Faca:</b> Bloqueia entradas em velas de rejeição contrárias e quedas livres superiores a 0.40%.", bullet_style))
    story.append(Paragraph("• <b>Sniper Mode (3 min):</b> Observa a exaustão da agressão no micro-momento para obter o melhor preço de execução.", bullet_style))
    story.append(Spacer(1, 8))

    # Seção 7
    story.append(Paragraph("7. 🔌 Infraestrutura e Telemetria", h1_style))
    story.append(Paragraph("• <b>Nuvem:</b> Execução 24/7 no Railway via Docker Linux com banco híbrido SQLite/PostgreSQL.", bullet_style))
    story.append(Paragraph("• <b>Comunicação:</b> Telegram Bot interativo com notificações instantâneas de PnL e painel web em NiceGUI.", bullet_style))

    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"[OK] PDF gerado com sucesso em: {output_filename}")

if __name__ == "__main__":
    build_pdf()
