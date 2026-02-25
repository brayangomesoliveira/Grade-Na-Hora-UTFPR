from __future__ import annotations

from src.core.state import AppStatus

PALETTE = {
    "bg0": "#0B0F14",
    "bg1": "#111827",
    "panel": "#161E2A",
    "panel_alt": "#1A2432",
    "border": "#263041",
    "text": "#E5E7EB",
    "muted": "#9CA3AF",
    "blue": "#2563EB",
    "blue_hover": "#1D4ED8",
    "blue_pressed": "#1E40AF",
    "green": "#10B981",
    "yellow": "#F59E0B",
    "red": "#EF4444",
    "cyan": "#22D3EE",
    "input_bg": "#0F172A",
    "tree_bg": "#0F1720",
    "tree_alt": "#121B28",
}


def app_stylesheet() -> str:
    p = PALETTE
    return f"""
    QWidget {{
        color: {p["text"]};
        font-family: 'Segoe UI', 'Inter', sans-serif;
        font-size: 12px;
    }}
    QMainWindow {{
        background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {p["bg0"]}, stop:1 {p["bg1"]});
    }}
    QWidget#RootWidget {{
        background: transparent;
    }}
    QFrame#TopBar {{
        background: rgba(22, 30, 42, 0.94);
        border: 1px solid {p["border"]};
        border-radius: 12px;
    }}
    QFrame#Card {{
        background: rgba(22, 30, 42, 0.95);
        border: 1px solid {p["border"]};
        border-radius: 12px;
    }}
    QFrame#CardTitleBar {{
        background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #1D4ED8, stop:1 #0EA5E9);
        border-top-left-radius: 11px;
        border-top-right-radius: 11px;
        border: none;
        min-height: 4px;
        max-height: 4px;
    }}
    QLabel#TitleLabel {{
        font-size: 18px;
        font-weight: 700;
    }}
    QLabel#MutedLabel {{
        color: {p["muted"]};
    }}
    QLabel#StatusBadge {{
        font-weight: 700;
        padding: 6px 10px;
        border-radius: 10px;
        background: #1F2937;
        border: 1px solid {p["border"]};
    }}
    QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox {{
        background: {p["input_bg"]};
        border: 1px solid {p["border"]};
        border-radius: 8px;
        padding: 6px 8px;
        selection-background-color: {p["blue"]};
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {{
        border: 1px solid {p["blue"]};
    }}
    QPushButton {{
        background: {p["panel_alt"]};
        border: 1px solid {p["border"]};
        border-radius: 10px;
        padding: 8px 12px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        border-color: {p["blue"]};
        background: #1E293B;
    }}
    QPushButton:pressed {{
        background: #0F172A;
    }}
    QPushButton#PrimaryButton {{
        background: {p["blue"]};
        border-color: {p["blue"]};
        color: white;
    }}
    QPushButton#PrimaryButton:hover {{
        background: {p["blue_hover"]};
        border-color: {p["blue_hover"]};
    }}
    QPushButton#PrimaryButton:pressed {{
        background: {p["blue_pressed"]};
        border-color: {p["blue_pressed"]};
    }}
    QPushButton#DangerButton {{
        background: rgba(239,68,68,0.18);
        border-color: rgba(239,68,68,0.55);
        color: #FCA5A5;
    }}
    QPushButton#DangerButton:hover {{
        background: rgba(239,68,68,0.28);
    }}
    QCheckBox {{
        spacing: 8px;
    }}
    QTreeWidget {{
        background: {p["tree_bg"]};
        alternate-background-color: {p["tree_alt"]};
        border: 1px solid {p["border"]};
        border-radius: 8px;
        outline: 0;
    }}
    QTreeWidget::item {{
        padding: 2px;
    }}
    QTreeWidget::item:selected {{
        background: rgba(37, 99, 235, 0.35);
        border: 1px solid rgba(37, 99, 235, 0.6);
    }}
    QHeaderView::section {{
        background: rgba(29, 78, 216, 0.22);
        border: none;
        border-right: 1px solid {p["border"]};
        border-bottom: 1px solid {p["border"]};
        padding: 6px;
        font-weight: 700;
    }}
    QSplitter::handle {{
        background: transparent;
        width: 8px;
    }}
    QProgressBar {{
        background: rgba(255,255,255,0.04);
        border: 1px solid {p["border"]};
        border-radius: 8px;
        text-align: center;
    }}
    QProgressBar::chunk {{
        background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #1D4ED8, stop:1 #22D3EE);
        border-radius: 7px;
    }}
    QScrollBar:vertical {{
        width: 12px;
        background: transparent;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        min-height: 24px;
        background: #263244;
        border-radius: 6px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    QToolTip {{
        background: #111827;
        color: {p["text"]};
        border: 1px solid {p["border"]};
        padding: 6px;
    }}
    """


def status_badge_style(status: AppStatus) -> str:
    color_map = {
        AppStatus.IDLE: "#6B7280",
        AppStatus.LOGGING: "#2563EB",
        AppStatus.SCRAPING: "#22C55E",
        AppStatus.READY: "#10B981",
        AppStatus.ERROR: "#EF4444",
        AppStatus.CANCELED: "#F59E0B",
    }
    color = color_map.get(status, "#6B7280")
    return f"background: rgba(255,255,255,0.04); border: 1px solid {color}; color: {color};"
