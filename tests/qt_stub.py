"""最小 PySide6 / pyperclip 替身，只為了讓 import 與類別定義能執行。"""
import sys
import types


class _Flag:
    def __init__(self, name="flag"):
        self.name = name

    def __or__(self, other):
        return _Flag(f"{self.name}|{getattr(other, 'name', other)}")

    __ror__ = __or__

    def __and__(self, other):
        return _Flag(f"{self.name}&{getattr(other, 'name', other)}")

    def __invert__(self):
        return _Flag(f"~{self.name}")

    def __eq__(self, other):
        return isinstance(other, _Flag) and other.name == self.name

    def __hash__(self):
        return hash(self.name)

    def __bool__(self):
        return True

    def __call__(self, *args, **kwargs):
        # Qt 有很多靜態工廠方法（QUrl.fromLocalFile、QKeySequence.fromString…），
        # 讓替身的屬性可以被呼叫，回傳帶著參數的新旗標以便測試斷言。
        if args:
            return _Flag(f"{self.name}({args[0]})")
        return _Flag(f"{self.name}()")

    def __str__(self):
        return self.name


class _AutoMeta(type):
    def __getattr__(cls, item):
        if item.startswith("__"):
            raise AttributeError(item)
        return _Flag(f"{cls.__name__}.{item}")


class Qt(metaclass=_AutoMeta):
    pass


class _BoundSignal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def disconnect(self, slot=None):
        self._slots.clear()

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


class Signal:
    """可當作類別屬性使用的訊號描述器。"""

    def __init__(self, *types_, **kwargs):
        self.types = types_
        self._name = None

    def __set_name__(self, owner, name):
        self._name = name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        store = obj.__dict__.setdefault("_stub_signals", {})
        key = self._name or id(self)
        if key not in store:
            store[key] = _BoundSignal()
        return store[key]


class _Base:
    def __init__(self, *args, **kwargs):
        self._stub_args = args

    def __getattr__(self, item):
        if item.startswith("__") or item == "_stub_args":
            raise AttributeError(item)
        return lambda *a, **k: None

    def __str__(self):
        # 讓測試能分辨「空的 QUrl()」和「帶路徑的 QUrl」
        args = getattr(self, "_stub_args", ())
        return str(args[0]) if args else f"{type(self).__name__}()"


def _make(name, bases=(_Base,)):
    # 用 _AutoMeta 當 metaclass，讓 QTextCursor.End 這種「類別層級」的常數
    # 存取也能運作（真正的 Qt 這些是 enum 成員）。
    return _AutoMeta(name, bases, {})


_CORE = [
    "QObject", "QRunnable", "QThreadPool", "QTimer", "QUrl", "QEvent", "QPoint",
    "QRect", "QSharedMemory", "QSize", "QMargins", "QThread", "QCoreApplication",
    "QAbstractNativeEventFilter", "QByteArray", "QMimeData", "QSettings",
]
_GUI = [
    "QAction", "QColor", "QFont", "QIcon", "QPainter", "QPixmap", "QCursor",
    "QGuiApplication", "QKeySequence", "QPalette", "QTextDocument", "QClipboard",
    "QScreen", "QFontMetrics", "QTextCursor", "QTextOption",
    "QBrush", "QStandardItemModel", "QStandardItem", "QPen",
]
_WIDGETS = [
    "QApplication", "QWidget", "QMainWindow", "QDialog", "QDialogButtonBox",
    "QComboBox", "QCheckBox", "QDoubleSpinBox", "QSpinBox", "QFormLayout",
    "QGroupBox", "QHBoxLayout", "QVBoxLayout", "QInputDialog", "QLabel",
    "QLineEdit", "QMessageBox", "QPlainTextEdit", "QPushButton", "QTabWidget",
    "QTextEdit", "QToolButton", "QFrame", "QSplitter", "QStatusBar",
    "QListWidget", "QListWidgetItem", "QMenu", "QSystemTrayIcon", "QStyle",
    "QSizePolicy", "QScrollArea", "QAbstractItemView",
]
_MULTIMEDIA = ["QAudioOutput", "QMediaPlayer", "QMediaDevices"]


def install() -> None:
    pyside = types.ModuleType("PySide6")
    pyside.__version__ = "6.0.0-stub"
    pyside.__path__ = []
    sys.modules["PySide6"] = pyside

    for mod_name, names in (("QtCore", _CORE), ("QtGui", _GUI),
                            ("QtWidgets", _WIDGETS), ("QtMultimedia", _MULTIMEDIA)):
        mod = types.ModuleType(f"PySide6.{mod_name}")
        for n in names:
            setattr(mod, n, _make(n))
        if mod_name == "QtCore":
            mod.Qt = Qt
            mod.Signal = Signal
            mod.Slot = lambda *a, **k: (lambda f: f)
            mod.Property = lambda *a, **k: (lambda f: f)
        if mod_name == "QtGui":
            mod.Qt = Qt
        sys.modules[f"PySide6.{mod_name}"] = mod
        setattr(pyside, mod_name, mod)

    clip = types.ModuleType("pyperclip")
    clip._buffer = [""]
    clip.copy = lambda v: clip._buffer.__setitem__(0, v)
    clip.paste = lambda: clip._buffer[0]
    sys.modules["pyperclip"] = clip
