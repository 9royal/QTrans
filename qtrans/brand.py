"""個人品牌符號 Ro。

跟 icons.py 一樣的原則：字串內嵌，不帶二進位檔案。打包成 exe 之後
檔案路徑會變（PyInstaller 把東西放進 _MEIPASS），外部檔案靠不住，
所以標誌直接寫在程式裡。

反白版不另外存一份，而是從黑色版轉出來 —— 兩份手動維護遲早會不同步。
"""
from __future__ import annotations

# 長寬比固定 520:334，任何情況都不得變形。
ASPECT = 520 / 334

MARK_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 280 520 334">
<defs>
<clipPath id="clip-0">
<path d="M 391 482 L 515.68 482 L 515.68 614 L 391 614 Z M 391 482"/>
</clipPath>
</defs>
<path class="fig" fill="#231816" d="M 351.05 326.44 C 357.75 358.13 331.31 392.7 291.98 403.65 C 252.66 414.6 215.35 397.8 208.65 366.11 C 201.95 334.42 228.39 299.85 267.72 288.89 C 307.04 277.94 344.35 294.75 351.05 326.44"/>
<path class="fig" fill="#231816" d="M 242.83 344.37 C 249.68 338.91 251.41 328.11 246.7 320.25 L 246.53 319.96 C 241.83 312.1 232.46 310.15 225.62 315.6 L 75.54 435.27 L 18.86 438.13 C 10.56 437.93 5.72 443.88 5.51 453.45 L 5.5 453.8 C 5.29 463.37 11.85 471.29 20.15 471.49 L 77.97 470.35 C 80.16 470.41 82.25 469.9 84.15 468.98 L 84.93 470.27 Z M 242.83 344.37"/>
<path class="fig" fill="#231816" d="M 383.06 591 C 375.97 590.27 370.75 582.94 371.4 574.64 L 372.12 565.47 C 372.06 565.5 372 565.54 371.94 565.57 C 355.38 574.13 335.52 564.14 326.79 543.24 L 326.74 543.26 L 253.13 347.29 L 229.26 359.62 L 303.72 557.86 L 303.79 557.83 C 318.91 593.86 354.71 610.32 385.16 594.59 C 386.84 593.72 388.46 592.75 390.04 591.73 Z M 383.06 591"/>
<path fill="#ffffff" d="M 513.68 547.53 C 513.68 580.59 487.59 607.4 455.41 607.4 C 423.22 607.4 397.13 580.59 397.13 547.53 C 397.13 514.46 423.22 487.66 455.41 487.66 C 487.59 487.66 513.68 514.46 513.68 547.53"/>
<g clip-path="url(#clip-0)">
<path fill="none" stroke-width="1" stroke="#231816" d="M 513.91 180.6 C 513.91 147.51 487.8 120.69 455.59 120.69 C 423.38 120.69 397.27 147.51 397.27 180.6 C 397.27 213.69 423.38 240.52 455.59 240.52 C 487.8 240.52 513.91 213.69 513.91 180.6 Z M 513.91 180.6" transform="matrix(0.99932, 0, 0, -0.99932, 0.128039, 728.008337)"/>
</g>
<path fill="#231816" d="M 426.99 529.68 L 432.71 507.27 L 421.17 499.08 C 413.15 505.07 406.67 513.11 402.47 522.46 L 405.36 530.68 Z M 467.3 560.12 L 472.3 537.53 L 453.8 525.3 L 437.37 540.32 L 445.71 561.84 Z M 430.19 579.52 L 424.51 598.3 C 432.06 603.16 440.77 606.29 450.11 607.15 L 459.05 599.91 L 452.16 578.45 Z M 509.94 526.38 L 496.64 523.14 L 485.62 542.38 L 500.97 559.67 L 513.29 554.53 C 513.55 552.23 513.68 549.9 513.68 547.53 C 513.68 540.08 512.36 532.96 509.94 526.38 M 474.42 564.79 L 463.4 584.03 L 478.75 601.32 L 486.88 597.92 C 491.24 595.05 495.2 591.59 498.65 587.66 L 496.58 570.19 Z M 418.48 542.49 L 397.28 543.3 C 397.18 544.7 397.13 546.11 397.13 547.53 C 397.13 556.57 399.09 565.14 402.58 572.83 L 409.34 577.23 L 426.75 563.43 Z M 489.15 503.34 L 485.3 496.13 C 476.77 490.88 466.83 487.81 456.2 487.67 L 453.08 509.04 L 473.39 519.11 Z M 489.15 503.34"/>
<path fill="none" stroke-width="1" stroke="#231816" d="M 496.79 157.93 L 501.18 168.45" transform="matrix(0.99932, 0, 0, -0.99932, 0.128039, 728.008337)"/>
<path fill="none" stroke-width="1" stroke="#231816" d="M 467.1 170.65 L 475.89 159.6" transform="matrix(0.99932, 0, 0, -0.99932, 0.128039, 728.008337)"/>
<path fill="none" stroke-width="1" stroke="#231816" d="M 445.89 166.28 L 450.49 143.02" transform="matrix(0.99932, 0, 0, -0.99932, 0.128039, 728.008337)"/>
<path fill="none" stroke-width="1" stroke="#231816" d="M 453.98 202.85 L 455.37 219.29" transform="matrix(0.99932, 0, 0, -0.99932, 0.128039, 728.008337)"/>
<path fill="none" stroke-width="1" stroke="#231816" d="M 432.87 220.89 L 453.26 219.12" transform="matrix(0.99932, 0, 0, -0.99932, 0.128039, 728.008337)"/>
<path fill="none" stroke-width="1" stroke="#231816" d="M 472.96 212.66 L 489.58 192.76" transform="matrix(0.99932, 0, 0, -0.99932, 0.128039, 728.008337)"/>
<path fill="none" stroke-width="1" stroke="#231816" d="M 430.35 148.59 L 416.28 157.39" transform="matrix(0.99932, 0, 0, -0.99932, 0.128039, 728.008337)"/>
<path fill="none" stroke-width="1" stroke="#231816" d="M 459.24 128.18 L 472.96 136.38" transform="matrix(0.99932, 0, 0, -0.99932, 0.128039, 728.008337)"/>
<path fill="none" stroke-width="1" stroke="#231816" d="M 416.28 201.34 L 416.28 181.71" transform="matrix(0.99932, 0, 0, -0.99932, 0.128039, 728.008337)"/>
<path fill="none" stroke-width="1" stroke="#231816" d="M 418.64 185.65 L 440.71 185.02" transform="matrix(0.99932, 0, 0, -0.99932, 0.128039, 728.008337)"/>
<path fill="none" stroke-width="1" stroke="#231816" d="M 472.49 190.61 L 490.56 183.92" transform="matrix(0.99932, 0, 0, -0.99932, 0.128039, 728.008337)"/>
<path class="fig" fill="#231816" d="M 401.26 577.44 C 401.26 586.2 393.46 593.31 383.84 593.31 C 374.21 593.31 366.41 586.2 366.41 577.44 C 366.41 568.67 374.21 561.56 383.84 561.56 C 393.46 561.56 401.26 568.67 401.26 577.44"/>
</svg>"""


def _to_white(svg: str) -> str:
    """反白版：人形與腿改白，再給足球加一圈白框讓它從深底分離。

    只換 class="fig" 那四條路徑。足球內部的五邊形要保持 #231816，
    否則球會變成一團白。
    """
    out = []
    for line in svg.splitlines(keepends=True):
        if 'class="fig"' in line:
            line = line.replace('fill="#231816"', 'fill="#ffffff"')
        if line.strip() == "</svg>":
            out.append('<circle cx="455.41" cy="547.53" r="59.6" fill="none" '
                       'stroke="#ffffff" stroke-width="3.5"/>\n')
        out.append(line)
    return "".join(out)


MARK_SVG_WHITE = _to_white(MARK_SVG)

_cache: dict = {}


def ro_pixmap(height: int = 20, dark: bool = False, ratio: float = 1.0):
    """畫出 Ro 符號。QtSvg 不在時回傳 None。

    標誌是簽名，缺了不影響任何功能，所以這裡絕對不能拋例外 ——
    為了一個裝飾性圖案讓翻譯程式起不來是本末倒置。
    """
    key = (height, dark, round(ratio, 2))
    if key in _cache:
        return _cache[key]
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPainter, QPixmap
        from PySide6.QtSvg import QSvgRenderer
    except Exception:
        return None

    try:
        source = MARK_SVG_WHITE if dark else MARK_SVG
        renderer = QSvgRenderer(source.encode("utf-8"))
        if not renderer.isValid():
            return None
        w = max(1, int(round(height * ASPECT * ratio)))
        h = max(1, int(round(height * ratio)))
        pm = QPixmap(w, h)
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing)
        renderer.render(painter)
        painter.end()
        pm.setDevicePixelRatio(ratio)
        _cache[key] = pm
        return pm
    except Exception:
        return None


def ro_label(height: int = 20, parent=None, opacity: float = 0.75):
    """做好一個可以直接塞進版面的 Ro 標籤；做不出來就回傳 None。

    深淺由實際的視窗底色決定，不看設定值 —— 使用者可能在系統層級
    切換深色模式，程式沒有自己的主題設定。
    """
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QLabel

        label = QLabel(parent)
        bg = label.palette().color(label.backgroundRole())
        pm = ro_pixmap(height, dark=bg.lightness() < 128,
                       ratio=label.devicePixelRatioF())
        if pm is None:
            return None
        label.setPixmap(pm)
        label.setFixedSize(int(round(height * ASPECT)), height)
        label.setScaledContents(True)
        label.setAccessibleName("Ro")
        label.setToolTip("Ro")
        if opacity < 1.0:
            from PySide6.QtWidgets import QGraphicsOpacityEffect

            effect = QGraphicsOpacityEffect(label)
            effect.setOpacity(opacity)
            label.setGraphicsEffect(effect)
        label.setCursor(Qt.ArrowCursor)
        return label
    except Exception:
        return None
