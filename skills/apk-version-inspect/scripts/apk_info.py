# -*- coding: utf-8 -*-
"""提取 APK 版本信息：解析二进制 AndroidManifest.xml (AXML)"""
import sys, os, zipfile, struct, hashlib, datetime


def parse_axml(data):
    """返回 (root_element_name, attrs_dict, strings_list)"""
    if len(data) < 8:
        return None, None, []
    if struct.unpack_from('<H', data, 0)[0] != 0x0003:   # RES_XML_TYPE
        return None, None, []
    offset = 8                      # 跳过 XML chunk header(8B)
    strings = []
    result = None
    while offset + 8 <= len(data):
        ctype = struct.unpack_from('<H', data, offset)[0]
        csize = struct.unpack_from('<I', data, offset + 4)[0]
        if csize < 8 or offset + csize > len(data):
            break

        if ctype == 0x0001:         # RES_STRING_POOL_TYPE
            string_count = struct.unpack_from('<I', data, offset + 8)[0]
            flags = struct.unpack_from('<I', data, offset + 16)[0]
            strings_start = struct.unpack_from('<I', data, offset + 20)[0]
            is_utf8 = (flags & (1 << 8)) != 0
            try:
                offs = struct.unpack_from('<%dI' % string_count, data, offset + 28)
            except Exception:
                break
            base = offset + strings_start
            for o in offs:
                p = base + o
                try:
                    if is_utf8:
                        n = data[p]; p += 1
                        if n & 0x80:
                            n = ((n & 0x7f) << 8) | data[p]; p += 1
                        l = data[p]; p += 1
                        if l & 0x80:
                            l = ((l & 0x7f) << 8) | data[p]; p += 1
                        strings.append(data[p:p + n].decode('utf-8', 'replace'))
                    else:
                        l = struct.unpack_from('<H', data, p)[0]; p += 2
                        if l & 0x8000:
                            l = ((l & 0x7fff) << 16) | struct.unpack_from('<H', data, p)[0]; p += 2
                        strings.append(data[p:p + l * 2].decode('utf-16-le', 'replace'))
                except Exception:
                    strings.append('')

        elif ctype == 0x0102 and result is None:   # RES_XML_START_ELEMENT_TYPE
            name_i = struct.unpack_from('<I', data, offset + 20)[0]
            attr_count = struct.unpack_from('<H', data, offset + 28)[0]
            attrs = {}
            ap = offset + 36
            for _ in range(attr_count):
                a_ns, a_name, a_raw, a_vsize, a_res0, a_type, a_data = \
                    struct.unpack_from('<IIIHBBI', data, ap)
                key = strings[a_name] if a_name < len(strings) else str(a_name)
                if a_type == 0x03:
                    val = strings[a_data] if a_data < len(strings) else ''
                elif a_type == 0x12:
                    val = bool(a_data)
                else:
                    val = a_data
                attrs[key] = val
                ap += 20
            result = (strings[name_i] if name_i < len(strings) else '', attrs)

        offset += csize

    if result:
        return result[0], result[1], strings
    return None, None, strings


def show(path):
    print("=" * 74)
    print("文件:", path)
    if not os.path.exists(path):
        print("  !! 不存在")
        return None
    st = os.stat(path)
    print("  大小    : %d 字节 (%.2f MB)" % (st.st_size, st.st_size / 1024 / 1024))
    print("  修改时间: " + datetime.datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M:%S'))
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    md5 = h.hexdigest()
    print("  MD5     :", md5)

    z = zipfile.ZipFile(path)
    el, attrs, strings = parse_axml(z.read('AndroidManifest.xml'))
    if attrs:
        keys = ['package', 'versionCode', 'versionName', 'compileSdkVersion',
                'compileSdkVersionCodename', 'platformBuildVersionCode',
                'platformBuildVersionName', 'minSdkVersion', 'targetSdkVersion']
        print("  ---------- manifest ----------")
        for k in keys:
            if k in attrs:
                print("  %-26s: %s" % (k, attrs[k]))
    else:
        print("  !! manifest 解析仍然失败")

    for extra in ['META-INF/com/android/build/gradle/app-metadata.properties',
                  'META-INF/version-control-info.textproto']:
        try:
            content = z.read(extra).decode('utf-8', 'replace').strip()
            print("  ---------- %s ----------" % extra)
            for line in content.splitlines()[:12]:
                print("     " + line)
        except Exception:
            pass
    print()
    return attrs


for p in sys.argv[1:]:
    show(p)
