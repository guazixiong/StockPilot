"""生成应用图标 stockpilot.ico（Qt 离屏渲染）。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import (QBrush, QColor, QFont, QGuiApplication,  # noqa: E402
                           QLinearGradient, QPainter, QPixmap)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    app = QGuiApplication([])
    pm = QPixmap(256, 256)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    grad = QLinearGradient(0, 0, 256, 256)
    grad.setColorAt(0.0, QColor("#2f81f7"))
    grad.setColorAt(1.0, QColor("#7b3ff2"))
    p.setBrush(QBrush(grad))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(12, 12, 232, 232), 52, 52)
    p.setPen(QColor("white"))
    font = QFont("Microsoft YaHei", 105, QFont.Bold)
    p.setFont(font)
    p.drawText(QRectF(0, -6, 256, 256), Qt.AlignCenter, "股")
    p.end()
    out = os.path.join(ROOT, "stockpilot.ico")
    if pm.save(out, "ICO"):
        print(f"icon saved: {out}")
        return 0
    print("icon save failed (Qt ICO writer unavailable); 跳过图标", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
