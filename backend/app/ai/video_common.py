"""图生视频跨 Provider 共享常量(T2-5:消除 video.py ↔ vidu.py 的重复/漂移)。

历史:语言→地区 的 word 映射曾在 video._REGION_HINT 与 vidu._REGION_PERSON 各写一份(值相同、易漂移)。
收成单一真相源;各 Provider 里"完整风格短语/UGC 段落"是各自内容(非漂移),仍留在各文件。
"""
from __future__ import annotations

# 语言 → 地区(人/氛围)。母帧本地风格随语言变,别写死巴西。单一真相源,video/vidu 共用。
LANGUAGE_REGION: dict[str, str] = {
    "葡萄牙语": "巴西",
    "英语": "欧美",
    "西班牙语": "拉美/西语区",
    "中文": "中国",
}
