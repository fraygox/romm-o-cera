#!/usr/bin/env python3
"""Adapt the pinned Cemu checkout for wuatool (run by build.sh, in the container).

Cemu's ETicketParser::GetTitleKey decrypts a disc's title key with the Wii U
common key Cemu builds in. wuatool keeps that key exactly as Cemu has it (it
comes from the pinned Cemu source at build time; this repository never holds
it). One line is added after it: wuatool_common_key_override() swaps in a
different key only when wuatool's test option names one, so the self-test and
RomM-o-Cera's tests can use synthetic discs made with random keys. A second
change (below) lets wuatool open a disc's other game partitions (update, DLC).

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

# FSTVolume::OpenFromDiscImage opens a disc's first GM (game) partition. Some discs
# also carry the game's update and DLC as GM partitions of their own; wuatool packs
# them beside the base, as Cemu's "Convert to .wua" does with installed ones. One
# condition is added where Cemu picks the GM partition: wuatool_gm_partition()
# sees every GM partition's index and name, and returns true for all of them
# (Cemu's own choice, the first) unless wuatool asks for another one by index.
path = src + "/Cafe/Filesystem/FST/FST.cpp"
with open(path, encoding="utf-8") as f:
    text = f.read()
pick = "if (gmPartitionIndex == std::numeric_limits<uint32>::max())\n"
if text.count(pick) != 1:
    sys.exit("patch-cemu: FST.cpp: the GM partition choice wasn't found exactly once")
text = text.replace(pick, "if (wuatool_gm_partition(i, (const uint8*)&name[0]) && "
                          "gmPartitionIndex == std::numeric_limits<uint32>::max())\n")
anchor = "// open WUD image\nFSTVolume* FSTVolume::OpenFromDiscImage(const fs::path& path, NCrypto::AesKey& discTitleKey,"
if text.count(anchor) != 1:
    sys.exit("patch-cemu: FST.cpp: OpenFromDiscImage wasn't found exactly once")
text = text.replace(anchor, "// wuatool: which GM partition to open (wuatool.cpp)\n"
                            "bool wuatool_gm_partition(uint32 index, const uint8* name);\n\n" + anchor)
with open(path, "w", encoding="utf-8") as f:
    f.write(text)
print("patch-cemu: GM partition choice hook added")
