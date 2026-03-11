"""
控制台输出兼容
==============
兼容 Windows `gbk` 控制台下的 Unicode 输出。
"""

import sys


def safe_print(*args, sep=" ", end="\n", file=None, flush=False):
    stream = file if file is not None else sys.stdout
    text = sep.join(str(arg) for arg in args)
    try:
        print(text, end=end, file=stream, flush=flush)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "utf-8"
        sanitized = text.encode(encoding, errors="replace").decode(
            encoding, errors="replace"
        )
        print(sanitized, end=end, file=stream, flush=flush)
