#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tv_adb.py — 从 PC 直连 Android TV（ADB over Wi-Fi）完成安装/校验/取证。
不需要 NAS、不需要 tvhelper、不需要 root。

用法:
    python tv_adb.py connect                        # 连接电视并打印设备信息
    python tv_adb.py verify [包名 ...]               # 列出已知应用在电视上的版本 + primaryCpuAbi
    python tv_adb.py install <包.apkm> [...] [--slim] [--dry-run]
                                                    # 解包 → install-multiple -r → 回读版本
    python tv_adb.py shot <输出.png>                 # 截图（先唤醒屏幕）
    python tv_adb.py forward <本地端口> <电视端口>    # 端口转发（例如转发电视 Clash 的 7890）
    python tv_adb.py log <包名> [秒数]                # 按 PID 抓目标应用日志
    python tv_adb.py downgrade <包.apkm> [...]       # 降级安装（回滚用，自动加 -d）
    python tv_adb.py apps                            # 列出电视上所有第三方应用

电视地址优先级：config.json > 环境变量 TV_ADDR > 内置默认值（示例地址，需自行修改）。
首次连接需要在电视上点『允许 USB 调试』（勾选『一律允许』后即永久生效）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(os.path.expanduser("~"), ".cache", "bravia-adb")
PT_DIR = os.path.join(CACHE, "platform-tools")


def _load_config() -> dict:
    """配置优先级：config.json > 环境变量 TV_ADDR > 内置默认值。

    config.json 放在 skill 根目录（scripts/ 的上一层），内容形如：
        {"tv_addr": "192.168.1.20:5555"}
    """
    for p in (os.path.join(HERE, "..", "config.json"),
              os.path.join(HERE, "config.json")):
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception as exc:  # 配置写坏了不该让整个脚本挂掉
                print(f"[warn] 读 {p} 失败，忽略：{exc}", file=sys.stderr)
    return {}


_CFG = _load_config()
TV = str(_CFG.get("tv_addr")
         or os.environ.get("TV_ADDR")
         or "192.168.1.20:5555")   # ← 示例地址；请改 config.json 或设 TV_ADDR

# 常见目标应用：包名 -> 显示名
KNOWN = {
    "com.amazon.amazonvideo.livingroom": "Prime Video (ATV)",
    "com.apple.atve.androidtv.appletv": "Apple TV (ATV)",
    "com.cbs.ott": "Paramount+ (ATV)",
    "com.disney.disneyplus": "Disney+ (ATV)",
    "com.google.android.youtube.tv": "YouTube (ATV)",
    "com.netflix.ninja": "Netflix (ATV)",
    "com.wbd.stream": "HBO Max (ATV)",
    "com.spotify.tv.android": "Spotify (ATV)",
}
# 注：com.spocky.projengmenu（Projectivy Launcher）用户不需要、已卸载，故不在清单内。

# 非本机架构的分片：32 位电视上用不到，--slim 时会剔除
_FOREIGN_ABI = re.compile(r"split_config\.(arm64_v8a|x86_64|x86|armeabi|riscv64)\.apk$", re.I)

_ADB: str | None = None


# ---------------------------------------------------------------- adb 定位
def _plat_key() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def _download_platform_tools() -> str:
    import urllib.request

    url = ("https://dl.google.com/android/repository/"
           f"platform-tools-latest-{_plat_key()}.zip")
    os.makedirs(CACHE, exist_ok=True)
    zp = os.path.join(CACHE, "platform-tools.zip")
    print(f"[setup] 本机没有 adb，正在下载官方 platform-tools ...\n        {url}")
    urllib.request.urlretrieve(url, zp)
    print("[setup] 解压中 ...")
    with zipfile.ZipFile(zp) as z:
        z.extractall(CACHE)
    os.remove(zp)
    exe = os.path.join(PT_DIR, "adb.exe" if os.name == "nt" else "adb")
    if os.name != "nt":
        os.chmod(exe, 0o755)
    print(f"[setup] 就绪: {exe}")
    return exe


def adb_exe() -> str:
    """按顺序找 adb：$ADB → PATH → 本 skill 的 .tools → 缓存 → 自动下载。"""
    global _ADB
    if _ADB:
        return _ADB

    exe = "adb.exe" if os.name == "nt" else "adb"
    cands = [
        os.environ.get("ADB", ""),
        shutil.which("adb") or "",
        os.path.abspath(os.path.join(HERE, "..", "tools", "platform-tools", exe)),
        os.path.abspath(os.path.join(os.getcwd(), ".tools", "platform-tools", exe)),
        os.path.join(PT_DIR, exe),
    ]
    for c in cands:
        if c and os.path.isfile(c):
            _ADB = c
            return _ADB

    _ADB = _download_platform_tools()
    return _ADB


# ---------------------------------------------------------------- 执行封装
def _run(args: list[str], binary_to: str | None = None):
    if binary_to:
        with open(binary_to, "wb") as fh:
            p = subprocess.run(args, stdout=fh, stderr=subprocess.PIPE)
        return "", p.stderr.decode("utf-8", "replace")
    p = subprocess.run(args, capture_output=True)
    return (p.stdout.decode("utf-8", "replace"),
            p.stderr.decode("utf-8", "replace"))


def raw(*args: str):
    """不带 -s 的 adb（connect / devices / start-server 用）。"""
    return _run([adb_exe(), *args])


def sh(*args: str, binary_to: str | None = None):
    """带 -s <TV> 的 adb。"""
    return _run([adb_exe(), "-s", TV, *args], binary_to=binary_to)


# `dumpsys package | grep -m1` 会因 grep 提前关管道而吐出 "Failed to write ... Broken pipe"，
# 这是无害噪音，但会污染解析结果，统一在这里滤掉。
_NOISE = ("Failed to write while dumping service", "Broken pipe")


def shell(cmd: str) -> str:
    out, err = sh("shell", cmd)
    text = (out + err).replace("\r", "")
    lines = [l for l in text.splitlines()
             if not any(n in l for n in _NOISE)]
    return "\n".join(lines).strip()


def _connect_quiet() -> bool:
    """确保已连接（每次调用都做：adb server 可能已重启）。"""
    out, err = raw("connect", TV)
    blob = out + err
    if "unauthorized" in blob:
        print("\n⚠️  电视未授权。请到电视上点『允许 USB 调试』"
              "（建议勾选『一律允许来自这台计算机』）后重新运行。\n", file=sys.stderr)
        return False
    out, err = raw("devices")
    if f"{TV}\tdevice" in out:
        return True
    host = TV.split(":")[0]
    print(
        f"\n⚠️  连不上 {TV}\n"
        f"    请依次检查：\n"
        f"      1) 电视已开机，且和这台电脑在同一局域网 —— 先试试：ping {host}\n"
        f"      2) 电视已开启「设置 → 系统 → 开发者选项 → 网络调试 / ADB 调试」\n"
        f"      3) 地址填对了 —— 写在 config.json 的 tv_addr 里，或设环境变量 TV_ADDR\n"
        f"    当前设备列表：{(out + err).strip().replace(chr(10), ' | ') or '(空)'}\n",
        file=sys.stderr,
    )
    return False


# ---------------------------------------------------------------- 子命令
def cmd_connect(_args):
    out, err = raw("connect", TV)
    print((out + err).strip())
    out, err = raw("devices", "-l")
    print(out.strip())
    if not _connect_quiet():
        return 1
    print("--- 设备信息 ---")
    for prop in ("ro.product.model", "ro.build.version.release",
                 "ro.build.version.sdk", "ro.product.cpu.abilist"):
        print(f"  {prop:<28} {shell(f'getprop {prop}')}")
    return 0


def cmd_verify(args):
    if not _connect_quiet():
        return 1
    pkgs = args.pkgs or list(KNOWN)
    print(f"{'应用':<24} {'versionName':<24} {'primaryCpuAbi':<16} 包名")
    print("-" * 100)
    for p in pkgs:
        name = KNOWN.get(p, "")
        v = shell(f"dumpsys package {p} | grep -m1 versionName")
        v = v.split("versionName=")[-1] if "versionName=" in v else "(未安装)"
        a = shell(f"dumpsys package {p} | grep -m1 primaryCpuAbi")
        a = a.split("primaryCpuAbi=")[-1] if "primaryCpuAbi=" in a else "-"
        print(f"{name:<24} {v:<24} {a:<16} {p}")
    return 0


def cmd_apps(_args):
    if not _connect_quiet():
        return 1
    print(shell("pm list packages -3"))
    return 0


def _pkg_from_name(path: str) -> str:
    """APKMirror 文件名以包名开头，例如 com.cbs.ott_16.21.0-...apkm"""
    base = os.path.basename(path)
    head = base.split("_")[0]
    return head if re.fullmatch(r"[a-zA-Z][a-zA-Z0-9._]+", head) else ""


def _extract(path: str, dest: str) -> list[str]:
    if path.lower().endswith((".apkm", ".xapk", ".zip", ".apks")):
        with zipfile.ZipFile(path) as z:
            z.extractall(dest)
        found = []
        for root, _dirs, files in os.walk(dest):
            for f in files:
                if f.lower().endswith(".apk"):
                    found.append(os.path.join(root, f))
        return sorted(found)
    return [os.path.abspath(path)]


def _do_install(paths, *, down=False, slim=False, dry=False) -> int:
    if not _connect_quiet():
        return 1
    rc = 0
    for path in paths:
        if not os.path.isfile(path):
            print(f"❌ 文件不存在: {path}")
            rc = 1
            continue
        pkg = _pkg_from_name(path)
        before = shell(f"dumpsys package {pkg} | grep -m1 versionName") if pkg else ""
        before = before.split("versionName=")[-1].strip() if "versionName=" in before else "(未安装)"

        tmp = tempfile.mkdtemp(prefix="tvapkm-")
        try:
            apks = _extract(path, tmp)
            if not apks:
                print(f"❌ 包里没找到 .apk: {path}")
                rc = 1
                continue
            if slim:
                kept = [a for a in apks if not _FOREIGN_ABI.search(os.path.basename(a))]
                dropped = [os.path.basename(a) for a in apks if a not in kept]
                if dropped:
                    print(f"   [slim] 剔除无关架构分片: {', '.join(dropped)}")
                apks = kept

            print(f"\n=== {os.path.basename(path)}")
            print(f"    分片 {len(apks)} 个: {', '.join(os.path.basename(a) for a in apks)}")
            flags = ["-r"] + (["-d"] if down else [])
            cmd = [adb_exe(), "-s", TV, "install-multiple", *flags, *apks]
            if dry:
                print("    [dry-run] " + " ".join(cmd))
                continue

            out, err = _run(cmd)
            blob = (out + err).strip()
            ok = "Success" in blob
            print(f"    安装: {'✅ Success' if ok else '❌ 失败'}"
                  + ("" if ok else f"\n    {blob[:400]}"))
            if not ok:
                rc = 1
                continue

            if pkg:
                after = shell(f"dumpsys package {pkg} | grep -m1 versionName")
                after = after.split("versionName=")[-1].strip() if "versionName=" in after else "?"
                abi = shell(f"dumpsys package {pkg} | grep -m1 primaryCpuAbi")
                abi = abi.split("primaryCpuAbi=")[-1].strip() if "primaryCpuAbi=" in abi else "?"
                flag = "→" if before != after else "=（同版本）"
                print(f"    回读: {before} {flag} {after}   primaryCpuAbi={abi}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return rc


def cmd_install(args):
    return _do_install(args.paths, slim=args.slim, dry=args.dry_run)


def cmd_downgrade(args):
    return _do_install(args.paths, down=True, dry=args.dry_run)


def cmd_shot(args):
    if not _connect_quiet():
        return 1
    shell("input keyevent KEYCODE_WAKEUP")
    time.sleep(1.2)
    out, err = sh("exec-out", "screencap", "-p", binary_to=args.out)
    size = os.path.getsize(args.out) if os.path.exists(args.out) else 0
    if size == 0:
        print("❌ 截图为 0 字节：电视上多半正显示系统对话框"
              "（如 USB 调试授权框），Sony 禁止对安全对话框截屏。")
        return 1
    print(f"✅ 截图已保存: {args.out} ({size} bytes)")
    return 0


def cmd_forward(args):
    if not _connect_quiet():
        return 1
    out, err = sh("forward", f"tcp:{args.local}", f"tcp:{args.remote}")
    print((out + err).strip())
    print(f"➜ 本机 127.0.0.1:{args.local} 已转发到 电视:{args.remote}")
    return 0


def cmd_log(args):
    if not _connect_quiet():
        return 1
    pid = shell(f"pidof {args.pkg}")
    if not pid:
        print(f"❌ {args.pkg} 没在运行，先启动它。")
        return 1
    pid = pid.split()[0]
    print(f"--- {args.pkg} (PID {pid}) 最近 {args.secs}s 日志 ---")
    sh("logcat", "-c")
    time.sleep(args.secs)
    out, _ = sh("logcat", "-d", "-v", "time")
    for line in out.replace("\r", "").splitlines():
        if re.search(rf"\(\s*{pid}\)", line):
            print(line)
    return 0


# ---------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(description="PC 直连 Android TV 的 ADB 工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("connect", help="连接电视并打印设备信息").set_defaults(func=cmd_connect)

    p = sub.add_parser("verify", help="列出版本与 primaryCpuAbi")
    p.add_argument("pkgs", nargs="*")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("install", help="解包并安装（保留数据）")
    p.add_argument("paths", nargs="+")
    p.add_argument("--slim", action="store_true", help="剔除 arm64/x86 等无关架构分片")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("downgrade", help="降级安装（回滚用，自动加 -d）")
    p.add_argument("paths", nargs="+")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_downgrade)

    p = sub.add_parser("shot", help="截图")
    p.add_argument("out")
    p.set_defaults(func=cmd_shot)

    p = sub.add_parser("forward", help="端口转发")
    p.add_argument("local", type=int)
    p.add_argument("remote", type=int)
    p.set_defaults(func=cmd_forward)

    p = sub.add_parser("log", help="按 PID 抓日志")
    p.add_argument("pkg")
    p.add_argument("secs", nargs="?", type=int, default=15)
    p.set_defaults(func=cmd_log)

    sub.add_parser("apps", help="列出第三方应用").set_defaults(func=cmd_apps)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
