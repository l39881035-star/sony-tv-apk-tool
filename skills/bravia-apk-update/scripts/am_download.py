# -*- coding: utf-8 -*-
"""APKMirror 自动下载器 v2 —— 带 CDP 下载事件监听，能看清下载 URL 与失败状态。

用法: python _am_dl2.py <清单json> [下载目录]
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

import websocket

CHROME = os.environ.get("CHROME") or r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9355
# Chrome profile 目录：保留可复用 cf_clearance（省掉一次过验证）。
# 可用环境变量 AM_PROFILE 指定，默认落在当前工作目录下。
PROFILE = os.environ.get("AM_PROFILE") or os.path.join(os.getcwd(), ".crprofile")

MARKS = ("请稍候", "Just a moment", "正在进行安全验证", "Attention Required",
         "you have been blocked", "Checking your browser", "Verifying you are human")

STEALTH = """
Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
window.chrome=window.chrome||{runtime:{}};
Object.defineProperty(navigator,'languages',{get:()=>['zh-CN','zh','en']});
Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
"""

BTN_JS = r"""
(() => {
  const a = document.querySelector('a.downloadButton');
  if (a && a.offsetParent !== null) { a.scrollIntoView({block:'center'}); a.click(); return 'btn:'+a.getAttribute('href'); }
  const b = Array.from(document.querySelectorAll('a')).find(x => /download\.php/i.test(x.getAttribute('href')||''));
  if (b) { return 'php:'+b.getAttribute('href'); }
  return 'none';
})()
"""


def walk(node, acc, d=0):
    if not isinstance(node, dict) or d > 12:
        return
    if (node.get("nodeName") or "").lower() in ("iframe", "frame"):
        acc.append(node)
    for k in ("children", "shadowRoots"):
        for c in (node.get(k) or []):
            walk(c, acc, d + 1)
    if node.get("contentDocument"):
        walk(node["contentDocument"], acc, d + 1)


def main():
    targets = json.load(open(sys.argv[1], encoding="utf-8"))
    outdir = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else "downloads").replace("\\", "/")
    os.makedirs(outdir, exist_ok=True)
    os.makedirs(PROFILE, exist_ok=True)

    proc = subprocess.Popen([
        CHROME, "--no-first-run", "--no-default-browser-check", "--disable-extensions",
        "--remote-allow-origins=*", "--remote-debugging-port=%d" % PORT,
        "--user-data-dir=" + PROFILE, "--window-size=1000,760", "--window-position=40,40",
        "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    events = []
    stop = threading.Event()

    def browser_listener():
        try:
            ver = json.loads(urllib.request.urlopen(
                "http://127.0.0.1:%d/json/version" % PORT, timeout=5).read())
            bws = websocket.create_connection(ver["webSocketDebuggerUrl"], timeout=5,
                                              max_size=1 << 22)
            bws.send(json.dumps({"id": 1, "method": "Browser.setDownloadBehavior",
                                 "params": {"behavior": "allow", "downloadPath": outdir,
                                            "eventsEnabled": True}}))
            bws.settimeout(1.0)
            while not stop.is_set():
                try:
                    m = json.loads(bws.recv())
                except Exception:
                    continue
                if m.get("method") in ("Browser.downloadWillBegin", "Browser.downloadProgress",
                                       "Network.responseReceived", "Network.loadingFailed"):
                    events.append(m)
        except Exception as e:
            events.append({"error": str(e)})

    try:
        ws_url = None
        for _ in range(80):
            try:
                for t in json.loads(urllib.request.urlopen(
                        "http://127.0.0.1:%d/json" % PORT, timeout=2).read()):
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        ws_url = t["webSocketDebuggerUrl"]
                        break
                if ws_url:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        if not ws_url:
            print("无法连接 Chrome")
            return 1

        th = threading.Thread(target=browser_listener, daemon=True)
        th.start()
        time.sleep(1.5)

        ws = websocket.create_connection(ws_url, timeout=90, max_size=64 * 1024 * 1024)
        seq = [0]

        def cmd(m, p=None, t=90):
            seq[0] += 1
            mid = seq[0]
            ws.send(json.dumps({"id": mid, "method": m, "params": p or {}}))
            end = time.time() + t
            while time.time() < end:
                try:
                    r = json.loads(ws.recv())
                except Exception:
                    return None
                if r.get("id") == mid:
                    return r
            return None

        def ev(e):
            r = cmd("Runtime.evaluate", {"expression": e, "returnByValue": True, "awaitPromise": True})
            return None if not r or "result" not in r else r["result"].get("result", {}).get("value")

        ws.settimeout(0.5)

        def drain(s):
            end = time.time() + s
            while time.time() < end:
                try:
                    ws.recv()
                except Exception:
                    pass

        def click(x, y):
            for fx, fy in ((x - 120, y - 60), (x - 25, y - 6), (x, y)):
                cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": fx, "y": fy,
                                                 "button": "none", "clickCount": 0})
                drain(0.1)
            cmd("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y,
                                             "button": "left", "clickCount": 1})
            drain(0.08)
            cmd("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y,
                                             "button": "left", "clickCount": 1})

        def find_widget():
            doc = cmd("DOM.getDocument", {"depth": -1, "pierce": True})
            if not doc or "result" not in doc:
                return None
            acc = []
            walk(doc["result"].get("root", {}), acc)
            for n in acc:
                nid = n.get("nodeId")
                if not nid:
                    continue
                q = cmd("DOM.getContentQuads", {"nodeId": nid})
                if not q or "result" not in q:
                    continue
                for pts in (q["result"].get("quads") or []):
                    xs = [pts[i] for i in range(0, len(pts), 2)]
                    ys = [pts[i] for i in range(1, len(pts), 2)]
                    x, y = min(xs), min(ys)
                    w, h = max(xs) - x, max(ys) - y
                    if w > 150 and 30 < h < 200 and y > 0:
                        return (x, y, w, h)
            return None

        def pass_challenge(dl=60):
            t0 = time.time()
            while time.time() - t0 < dl:
                t = ev("document.title") or ""
                if t and not any(m in t for m in MARKS):
                    return True
                b = find_widget()
                if b:
                    click(b[0] + 25, b[1] + b[3] / 2.0)
                    drain(2.5)
                else:
                    drain(1.2)
            return False

        cmd("Runtime.enable"); cmd("Page.enable"); cmd("DOM.enable"); cmd("Network.enable")
        cmd("Page.addScriptToEvaluateOnNewDocument", {"source": STEALTH})

        # 只保留本目标产生的事件
        results = []
        for tg in targets:
            name, url = tg["name"], tg["url"]
            mark = len(events)
            print("=" * 8, name, "=" * 8)
            cmd("Page.navigate", {"url": url})
            print("  过验证:", pass_challenge(55))

            for rnd in range(5):
                r = ev(BTN_JS) or "none"
                print("  [%d] %s" % (rnd, r[:130]))
                if r.startswith("php:"):
                    href = r[4:]
                    if not href.startswith("http"):
                        href = "https://www.apkmirror.com" + href
                    print("     直接导航到 download.php")
                    cmd("Page.navigate", {"url": href})
                    drain(4.0)
                elif r == "none":
                    if not pass_challenge(15):
                        drain(2.0)
                # 观测下载
                done = False
                for _ in range(30):
                    drain(2.0)
                    evs = [e for e in events[mark:] if e.get("method") == "Browser.downloadProgress"]
                    if evs:
                        st = evs[-1]["params"].get("state")
                        prog = evs[-1]["params"]
                        if st == "completed":
                            print("     ✅ 下载完成", prog.get("guid"))
                            done = True
                            break
                        if st == "canceled":
                            print("     ❌ 下载被取消", prog.get("guid"))
                            done = True
                            break
                if done:
                    break
            wb = [e["params"] for e in events[mark:] if e.get("method") == "Browser.downloadWillBegin"]
            pr = [e["params"] for e in events[mark:] if e.get("method") == "Browser.downloadProgress"]
            if wb and wb[0].get("url"):
                durl = wb[0]["url"]
                fname = wb[0].get("suggestedFilename") or "unknown.bin"
                tgt = os.path.join(outdir, fname)
                print("  >>> 用 curl 直取 R2 预签名 URL")
                rc = subprocess.run(["curl", "-sSL", "-o", tgt, "-w",
                                     "code=%{http_code} size=%{size_download} type=%{content_type}",
                                     "-m", "600", durl], capture_output=True, text=True)
                print("     curl:", rc.stdout.strip() or rc.stderr.strip()[:200])
                if os.path.exists(tgt):
                    print("     落盘:", fname, os.path.getsize(tgt), "字节")
            print("  downloadWillBegin:", json.dumps(wb, ensure_ascii=False)[:400])
            print("  downloadProgress :", json.dumps(pr, ensure_ascii=False)[:400])
            print("  --- 相关网络响应 ---")
            for e in events[mark:]:
                if e.get("method") == "Network.responseReceived":
                    r = e["params"].get("response", {})
                    u = r.get("url", "")
                    if "cloudflarestorage" in u or ".apkm" in u or "download" in u.lower():
                        print("   %s %s  ct=%s cl=%s" % (r.get("status"), r.get("statusText"),
                              r.get("mimeType"), (r.get("headers") or {}).get("content-length")))
                        print("      ", u[:150])
                if e.get("method") == "Network.loadingFailed":
                    print("   loadingFailed:", json.dumps(e["params"], ensure_ascii=False)[:250])
            results.append({"name": name, "willBegin": wb, "progress": pr})

        stop.set()
        print("\n===== 目录 =====")
        for f in sorted(os.listdir(outdir)):
            print("  ", f, os.path.getsize(os.path.join(outdir, f)))
    finally:
        stop.set()
        try:
            proc.terminate()
        except Exception:
            pass
        time.sleep(1.0)
        try:
            proc.kill()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
