#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在 lightworks 上用本机 mihomo 对节点做真实延迟测速。
独立进程、独立端口，不动正在跑的 Clash 链（7890/7891/9090）。
用法：python3 lw_speed_test.py test-config.yaml
输出：alive.json（按延迟排序的存活节点名+延迟）、speed-summary.json
"""
import json
import subprocess
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
import yaml

CFG = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("test-config.yaml")
WORK = Path("/tmp/speedtest")
WORK.mkdir(exist_ok=True)
BIN = "/tmp/mihomo/mihomo"
CTRL = "127.0.0.1:19090"
TEST_URL = "http://cp.cloudflare.com/"
TIMEOUT_MS = 5000
WORKERS = 64

cfg = yaml.safe_load(open(CFG, encoding="utf-8"))
proxies = cfg["proxies"]
print(f"loaded {len(proxies)} proxies", flush=True)

# name 去重（clash 要求唯一）
seen = set()
for i, p in enumerate(proxies):
    n = str(p.get("name", f"node-{i}"))
    if n in seen:
        n = f"{n}#{i}"
        p["name"] = n
    seen.add(n)
yaml.safe_dump(cfg, open(WORK / "config.yaml", "w", encoding="utf-8"),
               allow_unicode=True, sort_keys=False)

proc = subprocess.Popen([BIN, "-d", str(WORK), "-f", str(WORK / "config.yaml")],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
ready = False
for _ in range(90):
    time.sleep(4)
    try:
        r = requests.get(f"http://{CTRL}/proxies", timeout=8)
        if r.status_code == 200 and len(r.json().get("proxies", {})) >= len(proxies):
            ready = True
            break
    except Exception:
        pass
print("mihomo ready:", ready, flush=True)
if not ready:
    proc.terminate()
    sys.exit(1)


def delay_of(proxy):
    name = urllib.parse.quote(str(proxy["name"]), safe="")
    try:
        r = requests.get(f"http://{CTRL}/proxies/{name}/delay",
                         params={"timeout": TIMEOUT_MS, "url": TEST_URL},
                         timeout=TIMEOUT_MS / 1000 + 5)
        if r.status_code != 200:
            return (proxy, None)
        d = r.json().get("delay", 0)
        return (proxy, d if d else None)
    except Exception:
        return (proxy, None)


# 自检 3 个
for p in proxies[:3]:
    _, d = delay_of(p)
    print(f"  self-check {p.get('type')} {p.get('server')} -> {d}ms", flush=True)

results, done = [], 0
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    futs = {ex.submit(delay_of, p): p for p in proxies}
    for f in as_completed(futs):
        results.append(f.result())
        done += 1
        if done % 2000 == 0:
            print(f"  tested {done}/{len(proxies)}", flush=True)
proc.terminate()

ok = [(p, d) for p, d in results if d]
ok.sort(key=lambda x: x[1])
alive = [{"name": str(p["name"]), "type": p.get("type"), "server": p.get("server"),
          "delay_ms": d} for p, d in ok]
json.dump(alive, open(WORK / "alive.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
summary = {"total": len(proxies), "alive": len(ok),
           "p50_ms": ok[len(ok)//2][1] if ok else None,
           "p90_ms": ok[int(len(ok)*0.9)][1] if ok else None}
json.dump(summary, open(WORK / "speed-summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(summary, ensure_ascii=False), flush=True)
print("DONE", flush=True)
