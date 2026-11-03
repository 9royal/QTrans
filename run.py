"""QTrans 啟動進入點。

開發時執行：
    python run.py
平常使用（不要跳出主控台視窗）：
    pythonw run.py
"""
import sys

from qtrans.app import main

if __name__ == "__main__":
    sys.exit(main())
