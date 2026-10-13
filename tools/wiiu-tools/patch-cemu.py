#!/usr/bin/env python3
"""Adapt the pinned Cemu checkout for wuatool (run by build.sh, in the container).

Cemu's ETicketParser::GetTitleKey decrypts a disc's title key with the Wii U
common key Cemu builds in. wuatool keeps that key exactly as Cemu has it (it
comes from the pinned Cemu source at build time; this repository never holds
it). One line is added after it: wuatool_common_key_override() swaps in a
different key only when wuatool's test option names one, so the self-test and
RomM-o-Cera's tests can use synthetic discs made with random keys.

The change must apply exactly once, or the build stops. Nothing here prints,
stores or compares the key itself."""
import re
import sys

src = sys.argv[1]          # the Cemu checkout's src/ folder
path = src + "/Cemu/ncrypto/ncrypto.cpp"

with open(path, encoding="utf-8") as f:
    text = f.read()

init = re.compile(r"(\n([ \t]*)uint8 commonKey\[16\]\s*=\s*\{[^}]*\};)")
if len(init.findall(text)) != 1:
    sys.exit("patch-cemu: ncrypto.cpp: the common key initializer wasn't found exactly once")
text = init.sub(lambda m: m.group(1) + "\n" + m.group(2) +
                "wuatool_common_key_override(commonKey); // wuatool: tests only (wuatool.cpp)", text)

anchor = "namespace NCrypto\n"
if text.count(anchor) != 1:
    sys.exit("patch-cemu: ncrypto.cpp: 'namespace NCrypto' found %d times" % text.count(anchor))
text = text.replace(anchor, "// wuatool: replaces the common key only when its test option names one (wuatool.cpp)\n"
                            "void wuatool_common_key_override(uint8* key);\n\n" + anchor)

with open(path, "w", encoding="utf-8") as f:
    f.write(text)
print("patch-cemu: test-only common key override added")
