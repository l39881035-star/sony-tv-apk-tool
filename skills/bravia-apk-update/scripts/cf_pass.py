# -*- coding: utf-8 -*-
"""过 Cloudflare Turnstile 交互验证（模拟真人点勾），并复用 profile 保留 cf_clearance。

用法:
  python _cf_pass.py <url> [profile目录] [最长等待秒] [要执行的js文件]

要点（都是实测踩出来的）:
  1. 不要用 --disable-blink-features=AutomationControlled —— Chrome 会弹
     "您使用的是不受支持的命令行标记" 横幅，等于自曝。
     改用 Page.addScriptToEvaluateOnNewDocument 抹掉 navigator.webdriver。
  2. Turnstile 的勾选框在 iframe 内（跨域，拿不到 DOM），
     只能算 iframe 的 viewport 矩形，然后按相对偏移派发真实鼠标事件。
     实测勾选框中心 ≈ (iframe.x + 25, iframe.y + h/2)。
  3. 点之前先 mouseMoved 走两段，别瞬移点击。
  4. profile 必须复用，cf_clearance 才留得住（有效期约 30 分钟）。
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

import websocket

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9343

CHALLENGE_MARKS = ("请稍候", "Just a moment", "正在进行安全验证",
                   "Attention Required", "you have been blocked",
                   "Checking your browser", "Verifying you are human")

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || {runtime: {}};
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh','en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
"""

RECT_JS = r"""
(() => {
  const cand = Array.from(document.querySelectorAll('iframe')).map(f => {
    const r = f.getBoundingClientRect();
    return {x: r.x, y: r.y, w: r.width, h: r.height, src: f.src || ''};
  }).filter(o => o.w > 150 && o.h > 30 && o.h < 200 && o.y > 0);
  return JSON.stringify(cand);
})()
"""


def walk_nodes(node, acc, depth=0, maxdepth=12):
    """在 CDP DOM 树里找 iframe（pierce=true 时能穿进 shadow root）。"""
    if not isinstance(node, dict):
        return
    nm = (node.get("nodeName") or "").lower()
    if nm in ("iframe", "frame"):
        acc.append(node)
    if depth < maxdepth:
        for key in ("children", "shadowRoots"):
            for c in (node.get(key) or []):
                walk_nodes(c, acc, depth + 1, maxdepth)
        cd = node.get("contentDocument")
        if cd:
            walk_nodes(cd, acc, depth + 1, maxdepth)


def find_widget(cmd):
    """返回 Turnstile widget 的 (x, y, w, h)，找不到返回 None。"""
    doc = cmd("DOM.getDocument", {"depth": -1, "pierce": True})
    if not doc or "result" not in doc:
        return None
    acc = []
    walk_nodes(doc["result"].get("root", {}), acc)
    for n in acc:
        nid = n.get("nodeId")
        if not nid:
            continue
        q = cmd("DOM.getContentQuads", {"nodeId": nid})
        if not q or "result" not in q:
            continue
        quads = q["result"].get("quads") or []
        if not quads:
            continue
        pts = quads[0]
        xs = [pts[i] for i in range(0, len(pts), 2)]
        ys = [pts[i] for i in range(1, len(pts), 2)]
        x, y = min(xs), min(ys)
        w, h = max(xs) - x, max(ys) - y
        if w > 150 and 30 < h < 200 and y > 0:
            return (x, y, w, h)
    return None


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    url = sys.argv[1]
    profile = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.getcwd(), ".crprofile")
    maxwait = float(sys.argv[3]) if len(sys.argv) > 3 else 120.0
    js_file = sys.argv[4] if len(sys.argv) > 4 and os.path.isfile(sys.argv[4]) else None
    os.makedirs(profile, exist_ok=True)

    proc = subprocess.Popen([
        CHROME,
        "--no-first-run", "--no-default-browser-check", "--disable-extensions",
        "--remote-allow-origins=*",
        "--remote-debugging-port=%d" % PORT,
        "--user-data-dir=" + profile,
        "--window-size=1000,760", "--window-position=40,40",
        "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        ws_url = None
        for _ in range(80):
            try:
                tabs = json.loads(urllib.request.urlopen(
                    "http://127.0.0.1:%d/json" % PORT, timeout=2).read())
                for t in tabs:
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        ws_url = t["webSocketDebuggerUrl"]
                        break
                if ws_url:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        if not ws_url:
            print("无法连接 Chrome 调试端口")
            return 1

        ws = websocket.create_connection(ws_url, timeout=60, max_size=64 * 1024 * 1024)
        seq = [0]

        def cmd(method, params=None, timeout=60):
            seq[0] += 1
            mid = seq[0]
            ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
            end = time.time() + timeout
            while time.time() < end:
                try:
                    m = json.loads(ws.recv())
                except Exception:
                    return None
                if m.get("id") == mid:
                    return m
            return None

        def ev(expr):
            r = cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True})
            if not r or "result" not in r:
                return None
            return r["result"].get("result", {}).get("value")

        cmd("Runtime.enable")
        cmd("Page.enable")
        cmd("DOM.enable")
        cmd("Page.addScriptToEvaluateOnNewDocument", {"source": STEALTH_JS})
        cmd("Page.navigate", {"url": url})
        ws.settimeout(0.6)

        def drain(sec):
            end = time.time() + sec
            while time.time() < end:
                try:
                    ws.recv()
                except Exception:
                    pass

        def click(x, y):
            # 真人不会瞬移点击：先分两段移动
            for fx, fy in ((x - 120, y - 60), (x - 25, y - 6), (x, y)):
                cmd("Input.dispatchMouseEvent", {
                    "type": "mouseMoved", "x": fx, "y": fy,
                    "button": "none", "clickCount": 0})
                drain(0.12)
            cmd("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": x, "y": y,
                "button": "left", "clickCount": 1})
            drain(0.09)
            cmd("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": x, "y": y,
                "button": "left", "clickCount": 1})

        t0 = time.time()
        last_title = ""
        passed = False
        clicks = 0
        while time.time() - t0 < maxwait:
            title = ev("document.title") or ""
            if title != last_title:
                print("[%5.1fs] title=%s" % (time.time() - t0, title))
                last_title = title
            if title and not any(m in title for m in CHALLENGE_MARKS):
                passed = True
                break
            # 找 Turnstile widget 并点勾
            box = find_widget(cmd)
            if box is None:
                rects = ev(RECT_JS) or "[]"
                try:
                    cand = json.loads(rects)
                except Exception:
                    cand = []
                if cand:
                    f = cand[0]
                    box = (f["x"], f["y"], f["w"], f["h"])
            if box:
                bx, by, bw, bh = box
                click(bx + 25, by + bh / 2.0)
                clicks += 1
                drain(3.0)
            else:
                drain(1.5)

        print("--- 挑战%s：耗时 %.1fs，点击 %d 次 ---" % (
            "已通过" if passed else "未通过", time.time() - t0, clicks))

        out_js = open(js_file, "r", encoding="utf-8").read() if js_file else \
            "JSON.stringify({url:location.href,title:document.title,len:(document.body?document.body.innerText.length:0)})"
        r = cmd("Runtime.evaluate", {"expression": out_js, "returnByValue": True, "awaitPromise": True})
        if r and "result" in r:
            val = r["result"].get("result", {}).get("value")
            try:
                print(json.dumps(json.loads(val), ensure_ascii=False, indent=1))
            except Exception:
                print(val)
        print("[profile] " + profile)

        # 导出 cookie 成 curl 可用的 Netscape 格式
        cmd("Network.enable")
        ck = cmd("Network.getCookies", {"urls": [
            "https://www.apkmirror.com/",
            "https://downloadr2.apkmirror.com/",
            "https://downloadr3.apkmirror.com/",
        ]})
        jar = os.path.join(os.getcwd(), "_apkmirror_cookies.txt")
        n = 0
        if ck and "result" in ck:
            lines = ["# Netscape HTTP Cookie File"]
            for c in ck["result"].get("cookies", []):
                dom = c.get("domain", "")
                flag = "TRUE" if dom.startswith(".") else "FALSE"
                secure = "TRUE" if c.get("secure") else "FALSE"
                exp = int(c.get("expires") or 0)
                if exp <= 0:
                    exp = int(time.time()) + 3600
                lines.append("\t".join([dom, flag, c.get("path", "/"), secure,
                                        str(exp), c.get("name", ""), c.get("value", "")]))
                n += 1
            with open(jar, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        print("[cookies] %d 条 -> %s" % (n, jar))
        for c in (ck or {}).get("result", {}).get("cookies", []):
            print("   %s = %s" % (c.get("name"), (c.get("value") or "")[:18] + "..."))
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        time.sleep(0.8)
        try:
            proc.kill()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
