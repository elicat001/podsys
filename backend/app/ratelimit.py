"""极简内存滑动窗口限流器(无外部依赖)。

够用于单机:每个 key 维护一个时间戳队列,超窗口的丢弃。多实例部署时应换成
Redis 等共享存储。线程安全(同步端点跑在线程池)。
"""
from __future__ import annotations
import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_sec: float) -> bool:
        """记录一次命中;若窗口内已达 limit,返回 False(拒绝)。"""
        now = time.monotonic()
        with self._lock:
            dq = self._hits[key]
            while dq and now - dq[0] > window_sec:
                dq.popleft()
            if len(dq) >= limit:
                return False
            dq.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


# 注册限流器(进程级单例)
register_limiter = RateLimiter()
