#!/usr/bin/env python3
"""アンケートPDF 自動抽出・集計ツールのエントリポイント。

使い方の詳細は README.md を参照。
"""

import sys

from survey_extractor.cli import main

if __name__ == "__main__":
    sys.exit(main())
