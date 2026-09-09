"""UI v5 主题（StockPilot-UI-V5-Fusion-Terminal demo 全量移植）。

demo 视觉语言在 QSS 的落点：
- .simple-head → [eyebrow]/[pageTitle]/[pageSub]/[liveBadge]
- .panel-head  → [panelHead]/[panelTitle]/[panelSub]/[link]
- .overview-card/.ticker → [quote]/quoteValue/quoteChg
- .chart-tabs/.quick-tabs/.sector-tabs → [seg] 段钮（active 底部渐变亮块）
- .tab 徽章/.option-list → [pill]/[option]
所有设计令牌唯一定义处；改此处全局生效。
"""

# ------------------------------------------------------------ 设计令牌（demo:root 逐值）
BG = "#040913"          # --bg 深空底
PANEL = "#071526"       # --panel
PANEL2 = "#0A1A30"      # --panel2
CARD = "#0A1A30"        # 卡片（同 panel2 语义别名）
HOVER = "#102542"       # demo option/hover 底
BORDER = "#1E3B5E"      # --line 近似（rgba(81,144,223,.17) 实色化）
BORDER_SOFT = "#173351"  # 更弱的内部分隔
INPUT_BG = "#08182B"     # demo 输入底

ACCENT = "#4B98FF"      # --blue
ACCENT_HOVER = "#6FB0FF"
CYAN = "#4DDCC8"        # --cyan（积极/成功强调）
AI = "#8E6DFF"          # --purple（AI 专属）
AI_HOVER = "#A58FFF"
WARNING = "#FFB866"      # --gold
DANGER = "#FF5E77"      # --red
SUCCESS = "#41D8A6"     # --green

UP = "#FF5E77"          # 涨（红，demo .red）
DOWN = "#41D8A6"        # 跌（绿，demo .green）
FLAT = "#7D91AA"        # --muted 中性
TEXT = "#E7F0FF"        # --text
SUB = "#7D91AA"         # --muted
HINT = "#526780"        # demo small 色

# demo 图表配色（MA 曲线：金/紫/蓝/绿 —— demo chart-legend 逐值）
MA5 = "#FFB45D"
MA10 = "#A786FF"
MA20 = "#48A5FF"
MA60 = "#43D5A7"
CHART_BG = "#0A1A30"     # 图表底（与 panel2 融合）
CHART_GRID = "#14263D"  # 网格线（rgba(89,146,222,.09) 实色化）


class UISize:
    WINDOW_MIN_WIDTH = 1280
    WINDOW_MIN_HEIGHT = 800
    SIDEBAR_WIDTH = 180      # demo sidebar 180px
    TOPBAR_HEIGHT = 72       # demo topbar 72px
    PAGE_PADDING = 11         # demo #view padding
    PANEL_RADIUS = 12
    BUTTON_HEIGHT = 36
    TABLE_ROW_HEIGHT = 44


# demo 环境光背景：QSS 无法做 fixed radial，用深空线性渐变近似 app-bg。
# 页面容器（#view）再叠 panel 卡片渐变，层次与 demo 一致。
DARK_QSS = f"""
QWidget {{ background:{BG}; color:{TEXT};
  font-family:'Microsoft YaHei UI','PingFang SC','Segoe UI'; font-size:9pt; }}
QMainWindow, QDialog {{ background:{BG}; }}
QToolTip {{ background:{PANEL2}; color:{TEXT}; border:1px solid {BORDER};
  padding:6px 10px; border-radius:8px; font-size:8.5pt; }}

/* ==================== 页面版式（demo .simple-head） ==================== */
QFrame[pageHead="true"] {{ background:transparent; border:none; }}
QLabel[eyebrow="true"] {{ color:#4B7FB8; font-size:7pt; font-weight:bold;
  letter-spacing:2px; background:transparent; }}
QLabel[pageTitle="true"] {{ color:{TEXT}; font-size:15pt; font-weight:800;
  background:transparent; }}
QLabel[pageSub="true"] {{ color:#7186A2; font-size:8.5pt; background:transparent; }}
QLabel[liveBadge="true"] {{ color:#4ED9C5; font-size:8pt; font-weight:bold;
  letter-spacing:1px; background:transparent; }}

/* ==================== 面板（demo .panel） ==================== */
QFrame[card="true"] {{
  background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
  stop:0 rgba(10,28,51,219), stop:1 rgba(6,18,34,212));
  border:1px solid {BORDER}; border-radius:12px; }}
QFrame[card="true"] QLabel {{ background:transparent; }}
QFrame[panelHead="true"] {{ background:rgba(12,29,52,90);
  border:none; border-bottom:1px solid rgba(93,149,221,26); }}
QFrame[panelBody="true"] {{ background:transparent; border:none; }}
QLabel[panelTitle="true"] {{ color:#D9E8FF; font-size:10pt; font-weight:700;
  background:transparent; }}
QLabel[panelSub="true"] {{ color:#63799C; font-size:8pt; background:transparent; }}
QLabel[panelMore="true"] {{ color:#76B4FF; font-size:8pt; background:transparent; }}
QPushButton[link="true"] {{ background:transparent; border:none;
  color:#76B4FF; font-size:8pt; padding:2px 6px; }}
QPushButton[link="true"]:hover {{ color:#A5CBFF; }}
QLabel[hint="true"] {{ color:{HINT}; font-size:8pt; background:transparent; }}
QLabel[sub="true"] {{ color:{SUB}; background:transparent; }}
QLabel[title="true"] {{ color:#D9E8FF; font-size:10.5pt; font-weight:bold;
  background:transparent; }}
QGroupBox {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
  stop:0 rgba(10,28,51,219), stop:1 rgba(6,18,34,212));
  border:1px solid {BORDER}; border-radius:12px;
  margin-top:12px; padding:10px 8px 8px 8px; font-weight:bold; }}
QGroupBox::title {{ subcontrol-origin: margin; left:12px; padding:0 6px;
  color:#D9E8FF; background:transparent; }}

/* ==================== AI 面板（demo .ai-panel 紫光晕） ==================== */
QFrame[aiPanel="true"] {{
  background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
  stop:0 rgba(20,32,66,232), stop:1 rgba(12,20,44,225));
  border:1px solid rgba(88,126,255,71); border-radius:12px; }}

/* ==================== 统计卡（demo .quote/.data-card/.overview-card） ==================== */
QFrame[stat="true"] {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
  stop:0 rgba(18,45,81,174), stop:1 rgba(7,23,43,133));
  border:1px solid rgba(84,146,225,43); border-radius:10px; }}
QFrame[quote="true"] {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
  stop:0 rgba(18,45,81,174), stop:1 rgba(7,23,43,133));
  border:1px solid rgba(84,146,225,43); border-radius:10px; }}
QLabel[statValue="true"] {{ font-size:14pt; font-weight:800;
  background:transparent; }}
QLabel[statName="true"] {{ color:#8CA0B9; font-size:8.5pt; background:transparent; }}
QLabel[quoteValue="true"] {{ font-size:14pt; font-weight:800;
  background:transparent; }}
QLabel[quoteChg="true"] {{ font-size:8pt; font-weight:700;
  background:transparent; }}

/* ==================== 侧栏（demo .sidebar） ==================== */
QFrame#sidebar {{ background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
  stop:0 #050E1B, stop:1 #030711); border-right:1px solid {BORDER}; }}
QListWidget#nav {{ background:transparent; border:none; padding:14px 10px;
  font-size:9.5pt; outline:0; spacing:4px; }}
QListWidget#nav::item {{ color:#91A5BE; padding:13px 15px; margin:0;
  border-radius:9px; }}
QListWidget#nav::item:hover {{ background:rgba(29,70,126,63);
  color:#E6F1FF; }}
QListWidget#nav::item:selected {{
  background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
  stop:0 rgba(33,102,206,185), stop:1 rgba(37,74,148,69));
  color:#FFFFFF; font-weight:bold;
  border:1px solid rgba(89,154,255,33); }}
QLabel#brandTitle {{ color:{TEXT}; font-size:13pt; font-weight:bold;
  background:transparent; }}
QLabel#brandSub {{ color:#657995; font-size:7pt; background:transparent;
  letter-spacing:1px; }}

/* ==================== 顶栏（demo .topbar） ==================== */
QFrame#topbar {{ background:rgba(6,14,27,199); border-bottom:1px solid {BORDER}; }}
QFrame#topbar QLabel {{ background:transparent; }}
QLabel#tickerName {{ color:#7F94AE; font-size:7.5pt; background:transparent; }}
QLabel#ticker {{ background:transparent; }}
QFrame#tickerCard {{ background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
  stop:0 rgba(16,44,80,170), stop:1 rgba(9,24,46,120));
  border:1px solid rgba(84,146,225,43); border-radius:10px; }}
QFrame#chip {{ background:#10294B; border:1px solid {BORDER};
  border-radius:9px; }}
QFrame#chip QLabel {{ background:transparent; color:{SUB}; font-size:8pt; }}

/* ==================== 状态卡（demo .status-card） ==================== */
QFrame#statusCard {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
  stop:0 rgba(12,45,82,191), stop:1 rgba(7,23,44,179));
  border:1px solid rgba(63,134,226,51); border-radius:9px; }}

/* ==================== 段钮（demo .chart-tabs/.quick-tabs/.tab） ==================== */
QPushButton[seg="true"] {{ background:transparent; border:none;
  color:#7890AA; font-size:8.5pt; font-weight:normal;
  padding:6px 12px; border-radius:6px; }}
QPushButton[seg="true"]:hover {{ color:#AFC6E2; background:rgba(24,54,92,90); }}
QPushButton[seg="true"]:checked {{
  background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
  stop:0 rgba(43,116,215,107), stop:1 rgba(17,55,104,90));
  color:#8BC7FF; font-weight:bold; border-radius:6px; }}
QPushButton[pill="true"] {{ background:#102542; color:#8095AF;
  border:none; border-radius:7px; padding:6px 11px; font-size:8pt; }}
QPushButton[pill="true"]:hover {{ color:#BFD9F5; }}
QPushButton[pill="true"]:checked {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
  stop:0 #2B79D4, stop:1 #28569F); color:#CCE7FF; font-weight:bold; }}

/* ==================== 输入（demo 深底胶囊） ==================== */
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
  background:{INPUT_BG}; border:1px solid rgba(87,144,229,41);
  border-radius:8px; padding:6px 12px; color:{TEXT};
  selection-background-color:{ACCENT}; selection-color:#fff; }}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover
{{ border-color:rgba(87,144,229,90); }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus,
QDoubleSpinBox:focus {{ border-color:{ACCENT}; }}
QLineEdit[invalid="true"] {{ border-color:{DANGER}; }}
QLineEdit::placeholder-text {{ color:{HINT}; }}
QComboBox::drop-down {{ border:none; width:22px; }}
QComboBox QAbstractItemView {{ background:{PANEL2};
  border:1px solid {BORDER}; selection-background-color:{ACCENT}; outline:0;
  border-radius:8px; }}
QComboBox QAbstractItemView::item {{ padding:6px 12px; border-radius:6px; }}
QComboBox QAbstractItemView::separator {{ height:1px; background:{BORDER};
  margin:3px 8px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button,
QDoubleSpinBox::down-button {{ background:{PANEL2}; width:16px; border:none; }}
QCheckBox, QRadioButton {{ spacing:6px; background:transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{ width:14px; height:14px;
  border:1px solid {BORDER}; background:{INPUT_BG}; }}
QCheckBox::indicator {{ border-radius:4px; }}
QRadioButton::indicator {{ border-radius:7px; }}
QCheckBox:hover::indicator, QRadioButton:hover::indicator
{{ border-color:{ACCENT}; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
  background:{ACCENT}; border-color:{ACCENT}; }}

/* ==================== 按钮（demo 渐变主钮+次钮+紫 AI） ==================== */
QPushButton {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
  stop:0 #2B79D4, stop:1 #28569F); color:#FFFFFF; border:none;
  border-radius:8px; padding:8px 18px; font-weight:bold; }}
QPushButton:hover {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
  stop:0 #3B8AE8, stop:1 #2F65B4); }}
QPushButton:pressed {{ background:#1F4E93; }}
QPushButton:disabled {{ background:{PANEL}; color:{HINT};
  border:1px solid {BORDER}; }}
QPushButton[secondary="true"] {{ background:#102542; color:#A9C4E4;
  font-weight:normal; border:1px solid rgba(84,140,230,43); border-radius:8px; }}
QPushButton[secondary="true"]:hover {{ background:#1A3A66;
  border-color:rgba(120,170,255,90); }}
QPushButton[secondary="true"]:disabled {{ color:{HINT}; }}
QPushButton[danger="true"] {{ background:transparent; color:{DANGER};
  border:1px solid {DANGER}; border-radius:8px; font-weight:normal; }}
QPushButton[danger="true"]:hover {{ background:rgba(255,94,119,36); }}
QPushButton[success="true"] {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
  stop:0 #2AA87E, stop:1 #1E8A66); color:#04231A; border-radius:8px; }}
QPushButton[success="true"]:hover {{ background:#3BC492; }}
QPushButton[ai="true"] {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
  stop:0 #5B6CFF, stop:1 #8A5CFF); color:#FFFFFF;
  border-radius:18px; padding:8px 18px; font-weight:bold; }}
QPushButton[ai="true"]:hover {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
  stop:0 #7480FF, stop:1 #9B78FF); }}

/* ==================== 表格（demo .page-table：42px 表头 + 疏行距） ==================== */
QTableWidget {{ background:{PANEL}; gridline-color:rgba(93,149,221,20);
  border:1px solid {BORDER}; border-radius:10px;
  alternate-background-color:#0A1B2F; }}
QTableWidget::item {{ padding:8px 12px; border:none; }}
QTableWidget::item:hover {{ background:rgba(28,70,129,46); }}
QTableWidget::item:selected {{ background:rgba(35,95,176,120); color:#fff; }}
QHeaderView::section {{ background:rgba(13,33,58,140); color:#6D83A0;
  border:none; border-right:1px solid rgba(93,149,221,20);
  border-bottom:1px solid rgba(93,149,221,26); padding:9px;
  font-weight:normal; }}
QHeaderView::section:hover {{ color:{TEXT}; }}
QTableCornerButton::section {{ background:rgba(13,33,58,140); border:none;
  border-top-left-radius:10px; }}

/* ==================== 列表 / 文本 ==================== */
QListWidget, QTextBrowser {{ background:{PANEL}; border:1px solid {BORDER};
  border-radius:10px; }}
QTextEdit, QPlainTextEdit {{ background:{INPUT_BG};
  border:1px solid rgba(87,144,229,41); border-radius:10px; }}
QListWidget::item {{ padding:8px 10px; border-radius:7px; margin:1px 3px; }}
QListWidget::item:hover {{ background:rgba(29,70,126,46); }}
QListWidget::item:selected {{ background:rgba(35,95,176,120); color:#fff; }}

/* ==================== Tab（demo .chart-tabs 堆叠风） ==================== */
QTabWidget::pane {{ background:transparent; border:none; top:-1px; }}
QTabBar {{ background:transparent; }}
QTabBar::tab {{ background:transparent; color:#7890AA; padding:8px 16px;
  margin-right:2px; border:1px solid transparent; border-bottom:none;
  border-top-left-radius:7px; border-top-right-radius:7px; }}
QTabBar::tab:hover {{ color:{TEXT}; background:rgba(24,54,92,128); }}
QTabBar::tab:selected {{ background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
  stop:0 rgba(43,116,215,107), stop:1 rgba(17,55,104,77));
  color:#8BC7FF; font-weight:bold; }}

/* ==================== 进度 / 分割 / 滚动 ==================== */
QProgressBar {{ background:{INPUT_BG}; border:1px solid {BORDER};
  border-radius:6px; text-align:center; color:{TEXT}; }}
QProgressBar::chunk {{ border-radius:6px;
  background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
  stop:0 {ACCENT},stop:1 {AI}); }}
QSplitter::handle {{ background:{BG}; width:3px; height:3px; }}
QSplitter::handle:hover {{ background:{ACCENT}; }}
QScrollBar:vertical {{ background:transparent; width:9px; margin:0; }}
QScrollBar:handle:vertical {{ background:#23405F; border-radius:4px;
  min-height:30px; }}
QScrollBar:handle:vertical:hover {{ background:#2E5685; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height:0; width:0; }}
QScrollBar:horizontal {{ background:transparent; height:9px; margin:0; }}
QScrollBar:handle:horizontal {{ background:#23405F; border-radius:4px;
  min-width:30px; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background:transparent; }}

/* ==================== 状态栏 / 菜单 ==================== */
QStatusBar {{ background:rgba(5,12,23,240); color:{SUB};
  border-top:1px solid {BORDER}; }}
QMenu {{ background:{PANEL2}; border:1px solid {BORDER}; border-radius:10px;
  padding:5px; }}
QMenu::item {{ padding:7px 20px; border-radius:7px; }}
QMenu::item:selected {{ background:{ACCENT}; color:#fff; border-radius:7px; }}
QMenu::separator {{ height:1px; background:{BORDER}; margin:5px 10px; }}
QMenu::item:disabled {{ color:{HINT}; }}
"""

LIGHT_QSS = """
QWidget { background:#F2F5FA; color:#1C2B3F;
  font-family:'Microsoft YaHei UI','Segoe UI'; font-size:9pt; }
QFrame[card="true"] { background:#fff; border:1px solid #E2E8F0;
  border-radius:12px; }
QFrame#topbar { background:#fff; border-bottom:1px solid #E2E8F0; }
QListWidget#nav { background:transparent; border:none; }
QPushButton { background:#4B98FF; color:#fff; border:none;
  border-radius:8px; padding:8px 16px; }
QPushButton[secondary="true"] { background:#EAF0F8; color:#1C2B3F; }
QPushButton[danger="true"] { background:transparent; color:#FF5E77;
  border:1px solid #FF5E77; border-radius:8px; }
QTableWidget { background:#fff; border:1px solid #E2E8F0; border-radius:10px; }
"""


def apply_theme(app, name: str = "dark"):
    qss = DARK_QSS if name != "light" else LIGHT_QSS
    app.setStyleSheet(qss)
