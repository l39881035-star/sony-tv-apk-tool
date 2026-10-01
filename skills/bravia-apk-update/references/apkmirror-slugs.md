# APKMirror 页面结构与 slug 坑（实测记录）

## 应用页的正确读法

- 应用页顶部卡片的图片 alt 形如 `Netflix (Android TV) 13.2.0 (arm-v7a) (Android 7.0+)` —— 是「最新版 + 变体」，可作速查，但**别当唯一依据**。
- **权威位置：`### <App> variants` 段落**。每个变体一行，下面紧跟
  `Latest: [版本](链接) on 日期`。
  **这是判断「某个架构还有没有新版」的唯一可靠来源** —— 应用页顶部的「latest」只代表某一个变体。
- 「All versions」列表里混着不同变体的 release，**同一个版本号可能对应多个 release**（不同 arch/dpi/minapi）。
- **发布页**（`...-<版本>-release/`）的变体表才有 **versionCode**、架构、min API、DPI；**发布页没有 Package 字段**。
- **下载页**（`...-android-apk-download/`）才有 `Package: xxx`，用来核对包名。
- 一个 release 常有多个变体，URL 靠后缀区分：
  ```
  .../xxx-android-apk-download/      ← 第 1 个变体
  .../xxx-2-android-apk-download/    ← 第 2 个
  .../xxx-3-android-apk-download/    ← 第 3 个
  ```
  **后缀序号与变体行的顺序一一对应**，选错就装错架构/最低版本。

## 已实测踩到的 slug / 归属坑

| 应用 | 现象 |
|---|---|
| Netflix | `netflix-inc/netflix-android-tv` 正确。注意区分 `(arm-v7a)(Android 5.1+)` / `(Android 7.0+)` / `(Android 12+)` 三个变体，各自 latest 差很远（12+ 那个停在 2023 年的 10.0.6） |
| Prime Video | 正确 slug 是 `amazon-mobile-llc/prime-video-android-tv-android-tv`（**注意结尾重复的 `-android-tv`**）。`/apk/amazon/amazon-prime-video-android-tv/` 是 404 |
| YouTube for Android TV | 正确 slug 是 `google-inc/youtube-for-android-tv-android-tv`。用 `youtube-for-android-tv` 会**静默跳到完全无关的 "ATV Smart YouTube TV Bridge"**（不报 404，最容易骗过脚本） |
| Apple TV | `apple/apple-tv-android-tv` = **"Apple TV (Sony Android TV version)"，废弃列表，停在 2023 的 13.3.0**；正确列表是 `apple/apple-tv-android-tv-2`（"Apple TV: Shows, Movies & More (Android TV)"） |
| Paramount+ | 两个列表：`cbs-interactive-inc/paramount-2` = **`com.cbs.ott`**（正确）；`viacomcbs-streaming/paramount-android-tv` = `com.cbs.ca`（错误的） |
| HBO Max | `warnermedia-direct-llc/max-stream-hbo-tv-movies-android-tv` = **`com.wbd.stream`**（正确）；`.../hbo-max-stream-hbo-tv-movies-more` = `com.wbd.hbomax`（旧/手机版）。**标题几乎一样，必须核对包名** |
| Projectivy Launcher | 正确 slug 是 `spocky/projectivy-launcher-android-tv` |
| Disney+ | `disney/disney-android-tv` 正确。⚠️ TV 版与手机版**共用包名 `com.disney.disneyplus`**，靠列表区分 |
| Spotify TV | `spotify-ab/` 下**长期没找到** com.spotify.tv.android 的独立列表；只能靠 APKPure/APKFab/APKCombo/APKDer 四源交叉确认 |

## 用 r.jina.ai 读页面的注意点

- 限流表现：返回 `Title: Just a moment...` 且正文约 500 字节 → 等 60–75 秒再试；正常请求间隔 ≥7 秒。
- 只给文本/markdown，**拿不到二进制文件**，所以只能用来看版本，不能用来下载 APK。

## 快速定位 slug 的办法（省时间）

1. 先 `WebSearch`：`apkmirror "<应用全名>" apk`，搜索结果里常直接给出带完整路径的 release 页面 URL。
2. 拿 release URL 反推应用页：砍掉 `/<版本>-release/` 之后的部分。
3. 再用 `r.jina.ai` 打开应用页，从 variants 段落确认架构与 latest，并核对 `Package:`。
