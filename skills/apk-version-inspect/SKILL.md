---
name: apk-version-inspect
description: 在没装 Android SDK / aapt / apktool 的情况下，用纯 Python 读出一个 APK 的版本号（versionName + versionCode）、包名、编译 SDK，并对比两个 APK 谁更新。当用户说"看不到 APK 版本号""这两个 APK 哪个新""对比一下这两个 apk"时使用。内含两个实测踩过的二进制解析坑。
---

# APK 版本号速查（无 Android SDK 环境）

## 何时用

- 用户给了两个（或多个）APK，想知道谁的版本更新
- 用户在文件管理器里"看不到版本号"（正常现象：APK 的版本信息在**二进制的** `AndroidManifest.xml` 里，不是明文，资源管理器读不出来）
- 机器上没有 `aapt` / `aapt2` / `apktool` / `androguard`

## 快速用法

```bash
python scripts/apk_info.py "路径A.apk" "路径B.apk"
```

输出：文件大小、修改时间、MD5、包名、`versionCode`、`versionName`、`compileSdkVersion`，
以及 `META-INF/com/android/build/gradle/app-metadata.properties` 与 `version-control-info.textproto`
（后者含 git revision，可用来确认两次构建是不是同一提交）。

判断新旧：**以 `versionCode` 为准**，不要只看 `versionName`。
（实测遇到过 `versionCode` 从 301 跳到 315 而 `versionName` 只从 3.0.1 到 3.0.2 的情况，中间隔了多次内部构建。）

## 两个必须知道的解析坑

### 坑 1：chunk header 不是 3 个 uint32

AXML 里所有 chunk 的头部是：

```
uint16 type       // 低 16 位
uint16 headerSize // 高 16 位
uint32 size       // 该 chunk 总长度
```

把它当成 `<III` 读会**整体错位**，表现为"魔数正确但解析不出任何东西"。
正确写法：

```python
ctype = struct.unpack_from('<H', data, offset)[0]
csize = struct.unpack_from('<I', data, offset + 4)[0]
offset += csize
```

也只有用 `<I` 一次性读前 4 字节时，才会看到 `0x00080003` 这个"魔数"（= type 0x0003 + headerSize 0x0008）。

根 chunk 是 `RES_XML_TYPE`，读完之后**要从 offset 8 开始**遍历子 chunk（`RES_STRING_POOL_TYPE = 0x0001`、`RES_XML_START_ELEMENT_TYPE = 0x0102`）。
根 chunk 的 `size` 字段等于整个文件长度，照搬会让循环立刻结束。

### 坑 2：属性是 20 字节，格式 `<IIIHBBI`

```
ns(4) + name(4) + rawValue(4) + size(uint16) + res0(uint8) + dataType(uint8) + data(4)
```

写成 `<IIIIHBBI`（4 个 I）会报 `ValueError: too many values to unpack`。
`dataType == 0x03` 表示值是字符串（取 string pool 的索引），`0x12` 是布尔，其余按整数处理。

## 其他要点

- String pool 的 `stringsStart` 是**相对该 chunk 起点**的偏移，字符串数据基址 = `chunk_offset + stringsStart`（不要再加 8）。
- `flags & (1 << 8)` 为真才是 UTF-8 编码，否则是 UTF-16LE。
- 想看两个 APK 的**内容差异**（比如新版加了什么 native 库），直接比对 `zipfile.namelist()` 的两个集合即可；`lib/` 目录下新增 `.so` 往往意味着加了新功能模块。
- 路径含中文时，用 Python 直接传参没问题（Git Bash 下也正常）。

## 参考实测案例

某 TV 版视频应用两个同名 APK，文件名完全一样、看不出差别：

| | A | B |
|---|---|---|
| 大小 | 134.31 MB | 144.14 MB |
| versionCode | 301 | **315** |
| versionName | 3.0.1 | **3.0.2** |

结论靠 `versionCode` 得出（315 > 301）。内容差异上，新版新增了 `libarchive-jni.so`（4 个架构）与 jsoup 依赖，
`classes.dex` 增大 1.83 MB —— 说明功能性更新，不是仅重新打包。

## 相关 skill

- **`bravia-apk-update`** —— 本 skill 只管「本地怎么读版本」；那个 skill 管「去 APKMirror 核对有没有新版 → 下载 → 推到 Android TV（经飞牛 NAS 的 tvhelper）」。两者配合：先用本 skill 读出本地 `versionCode` 作基线，再跑那个 skill 的完整流水线。它内含 APKMirror 的 slug / 归属坑清单（同名列表、包名撞车、废弃列表）与 Cloudflare 抓包方案。
