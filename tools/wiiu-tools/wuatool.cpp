// wuatool: Wii U disc image (.wud / .wux) -> .wua (Cemu's ZArchive game archive),
// and the checks RomM-o-Cera runs before it deletes the image.
//
// Built on Cemu's own disc code (src/Cafe/Filesystem/FST, WUD, ncrypto; MPL-2.0)
// and ZArchive (MIT-0), so the archive holds exactly the files Cemu reads from
// the disc, in the layout Cemu's own "Convert to .wua" writes
// ("<title id>_v<version>/code|content|meta/..."). See tools/wiiu-tools/ in
// https://github.com/fraygox/romm-o-cera. This file: MPL-2.0.
//
//   wuatool pack   [-k keys]... <disc.wud|wux> <out.wua>
//   wuatool verify [-k keys]... <disc.wud|wux> <game.wua>
//   wuatool probe  [-k keys]... <disc.wud|wux>
//   wuatool check  <game.wua>
//   wuatool cat    <game.wua> <path in it>   (one file to stdout)
//   wuatool wux    <disc.wud> <out.wux>      (the lossless .wux container; no keys)
//   wuatool unwux  <disc.wux> <out.wud>      (back to the plain image)
//   wuatool compare <disc.wud|wux> <disc.wud|wux>   (same bytes, as Cemu reads them)
//   wuatool hash   <disc.wud|wux>            (size, CRC32, MD5, SHA-1 of the image)
//   wuatool --version
//
// -k: disc keys, in Cemu's keys.txt format (one 32-digit hex key per line, '#'
// or ';' starts a comment) or a 16-byte key file; each is tried against the disc
// as Cemu does. The Wii U common key is Cemu's own, built in from the pinned Cemu
// source. -T <file> (tests only) replaces it with the one key in that file.
//
// Exit codes: 0 ok, 1 usage, 2 not a readable Wii U game disc, 3 no disc key,
// 5 the disc can't be read to the end (damaged or cut short), 6 the archive
// can't be written, 7 the archive doesn't match the disc, 8 the archive is
// damaged (a .wua, or a .wux whose layout isn't whole).
#include "Common/precompiled.h"
#include "Cafe/Filesystem/FST/FST.h"
#include "Cafe/Filesystem/WUD/wud.h"
#include "util/crypto/aes128.h"
#include "zarchive/zarchivewriter.h"
#include "zarchive/zarchivereader.h"
#include "zarchive/zarchivecommon.h"
#include <openssl/evp.h>
#include <cstdio>
#include <cerrno>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#include <unordered_map>
#include <algorithm>

#ifndef WUATOOL_VERSION
#define WUATOOL_VERSION "dev"
#endif

typedef std::array<uint8, 16> Key128;

enum Exit { E_OK = 0, E_USAGE = 1, E_DISC = 2, E_DISCKEY = 3, E_READ = 5, E_WRITE = 6,
			E_DIFFERS = 7, E_ARCHIVE = 8 };

// ------------------------------------------------------------------ keys

static std::vector<Key128> g_keys;              // from -k: disc key candidates

static bool parseHex(const std::string& s, Key128& k)
{
	if (s.size() != 32)
		return false;
	for (int i = 0; i < 16; i++)
	{
		char* end = nullptr;
		std::string b = s.substr(i * 2, 2);
		if (!isxdigit((unsigned char)b[0]) || !isxdigit((unsigned char)b[1]))
			return false;
		k[i] = (uint8)strtoul(b.c_str(), &end, 16);
	}
	return true;
}

// Cemu's keys.txt rules (KeyCache.cpp): cut at '#' or ';', drop spaces, tabs, '-' and
// '_', then a line of exactly 32 hex digits is a key. A file of exactly 16 bytes
// that isn't such a line is a raw key (Redump's .key files).
static bool loadKeyFile(const std::string& path)
{
	FILE* f = fopen(path.c_str(), "rb");
	if (!f)
	{
		fprintf(stderr, "wuatool: can't read %s (%s)\n", path.c_str(), strerror(errno));
		return false;
	}
	std::string data;
	char buf[65536];
	size_t n;
	while ((n = fread(buf, 1, sizeof(buf), f)) > 0 && data.size() < (64u << 20))
		data.append(buf, n);
	fclose(f);
	size_t before = g_keys.size();
	size_t pos = 0;
	while (pos < data.size())
	{
		size_t eol = data.find('\n', pos);
		if (eol == std::string::npos)
			eol = data.size();
		std::string line = data.substr(pos, eol - pos), clean;
		pos = eol + 1;
		for (char c : line)
		{
			if (c == '#' || c == ';')
				break;
			if (c == ' ' || c == '\t' || c == '-' || c == '_' || c == '\r')
				continue;
			clean += c;
		}
		Key128 k;
		if (parseHex(clean, k))
			g_keys.push_back(k);
	}
	if (g_keys.size() == before && data.size() == 16)
	{
		Key128 k;
		memcpy(k.data(), data.data(), 16);
		g_keys.push_back(k);
	}
	return true;
}

// Cemu's FST code asks for disc keys by index (replaces Cemu's KeyCache.cpp)
void KeyCache_Prepare() {}
uint8* KeyCache_GetAES128(sint32 index)
{
	return index >= 0 && index < (sint32)g_keys.size() ? g_keys[index].data() : nullptr;
}

// Cemu's ETicketParser::GetTitleKey uses the Wii U common key Cemu builds in (taken
// from the pinned Cemu source at build time). patch-cemu.py adds one call after it:
// with -T (tests only: synthetic discs made with random keys) that key is replaced.
static bool g_testCommon = false;
static Key128 g_testCommonKey;

void wuatool_common_key_override(uint8* key)
{
	if (g_testCommon)
		memcpy(key, g_testCommonKey.data(), 16);
}

static uint32 be32(const uint8* p) { return ((uint32)p[0] << 24) | ((uint32)p[1] << 16) | ((uint32)p[2] << 8) | p[3]; }

static bool loadTestCommonKey(const char* path)
{
	size_t before = g_keys.size();
	if (!loadKeyFile(path) || g_keys.size() != before + 1)
	{
		fprintf(stderr, "wuatool: -T needs a file holding exactly one key\n");
		return false;
	}
	g_testCommonKey = g_keys.back();
	g_keys.pop_back();
	g_testCommon = true;
	return true;
}

// ------------------------------------------------------------------ the disc

static int openDisc(const char* path, FSTVolume** out)
{
	*out = nullptr;
	wud_t* wud = wud_open(fs::path(path));
	if (!wud)
	{
		fprintf(stderr, "wuatool: can't open %s\n", path);
		return E_DISC;
	}
	uint8 magic[4]{};
	bool isDisc = wud_readData(wud, magic, 4, 0x10000) == 4 && be32(magic) == 0xCC549EB9;
	wud_close(wud);
	if (!isDisc)
	{
		fprintf(stderr, "wuatool: not a Wii U disc image\n");
		return E_DISC;
	}
	FSTVolume::ErrorCode ec = FSTVolume::ErrorCode::UNKNOWN_ERROR;
	FSTVolume* v = FSTVolume::OpenFromDiscImage(fs::path(path), &ec);
	if (v)
	{
		*out = v;
		return E_OK;
	}
	if (ec == FSTVolume::ErrorCode::DISC_KEY_MISSING)
	{
		fprintf(stderr, "wuatool: no disc key for this disc (%zu keys tried)\n", g_keys.size());
		return E_DISCKEY;
	}
	fprintf(stderr, "wuatool: the disc's game partition can't be read (damaged image?)\n");
	return E_DISC;
}

static std::string xmlTag(const std::string& xml, const char* tag)
{
	std::string open = std::string("<") + tag;
	size_t p = xml.find(open);
	if (p == std::string::npos)
		return "";
	p = xml.find('>', p);
	if (p == std::string::npos)
		return "";
	size_t e = xml.find("</", p);
	if (e == std::string::npos)
		return "";
	std::string v = xml.substr(p + 1, e - p - 1);
	v.erase(0, v.find_first_not_of(" \t\r\n"));
	v.erase(v.find_last_not_of(" \t\r\n") + 1);
	return v;
}

// "0005000010101c00_v16": the archive's top folder, as Cemu names it
static bool titleFolder(FSTVolume* v, std::string& out)
{
	bool ok = false;
	std::vector<uint8> d = v->ExtractFile("code/app.xml", &ok);
	if (!ok || d.empty())
		return false;
	std::string xml(d.begin(), d.end());
	std::string tid = xmlTag(xml, "title_id"), ver = xmlTag(xml, "title_version");
	if (tid.size() != 16 || tid.find_first_not_of("0123456789abcdefABCDEF") != std::string::npos)
		return false;
	if (ver.empty() || ver.size() > 5 || ver.find_first_not_of("0123456789") != std::string::npos)
		return false;
	unsigned long long id = strtoull(tid.c_str(), nullptr, 16);
	unsigned long version = strtoul(ver.c_str(), nullptr, 10);
	if ((id >> 48) != 0x0005 || version > 0xFFFF)
		return false;
	out = fmt::format("{:016x}_v{}", id, version);
	return true;
}

// Every file and folder of the game partition in FST order, as Cemu mounts it
// (entries flagged as links are hidden from the game, so they're left out).
struct Walker
{
	FSTVolume* vol;
	std::function<bool(const std::string& path, FSTFileHandle& h)> onFile;
	std::function<bool(const std::string& path)> onDir;
	bool walk(const std::string& dir)
	{
		FSTDirectoryIterator it;
		if (!vol->OpenDirectoryIterator(dir, it))
		{
			fprintf(stderr, "wuatool: can't open folder '%s' on the disc\n", dir.c_str());
			return false;
		}
		FSTFileHandle h;
		while (vol->Next(it, h))
		{
			std::string name(vol->GetName(h));
			if (name.empty() || name == "." || name == ".." || name.find('/') != std::string::npos ||
				name.find('\\') != std::string::npos)
			{
				fprintf(stderr, "wuatool: unexpected name on the disc in '%s'\n", dir.c_str());
				return false;
			}
			if (vol->HasLinkFlag(h))
				continue;
			std::string p = dir.empty() ? name : dir + "/" + name;
			if (vol->IsDirectory(h))
			{
				if (onDir && !onDir(p))
					return false;
				if (!walk(p))
					return false;
			}
			else if (!onFile(p, h))
				return false;
		}
		return true;
	}
};

struct Progress
{
	uint64 total = 0, done = 0;
	int last = -1;
	const char* what = "";
	void add(uint64 n)
	{
		done += n;
		int pct = total ? (int)(done * 100 / total) : 100;
		if (pct != last)
		{
			last = pct;
			fprintf(stderr, "%s %d%%\n", what, pct);
		}
	}
};

static uint64 gameSize(FSTVolume* v, uint32& files)
{
	uint64 total = 0;
	files = 0;
	Walker w{v};
	w.onFile = [&](const std::string&, FSTFileHandle& h) { total += v->GetFileSize(h); files++; return true; };
	w.walk("");
	return total;
}

// ------------------------------------------------------------------ the disc's titles
//
// A disc's partition table lists SI (tickets), sometimes UP (console firmware), and
// one GM partition per title, named "GM" + its title ID: the game (00050000...),
// and on some discs its update (0005000E...) and DLC (0005000C...). Cemu opens the
// first GM partition (FST.cpp: "we use the first GM partition we find"). A .wua
// holds one "<title id>_v<version>" folder per title (Cemu's TitleInfo.cpp
// ParseWuaTitleFolderName), as Cemu's own "Convert to .wua" writes base, update and
// DLC into one archive; launching it adds every title in it (TitleList.cpp
// AddTitleFromPath), so the game runs with the bundled update. patch-cemu.py lets
// wuatool open each GM partition by index; the update and DLC go in beside the game.

static int g_wantGM = -1;                                    // GM partition to open; -1: the first (Cemu's)
static std::vector<std::pair<uint32, std::string>> g_gms;    // every GM partition the last open saw

bool wuatool_gm_partition(uint32 index, const uint8* name)
{
	std::string n((const char*)name, strnlen((const char*)name, 31));
	if (std::none_of(g_gms.begin(), g_gms.end(), [&](auto& g) { return g.first == index; }))
		g_gms.emplace_back(index, n);
	return g_wantGM < 0 || (uint32)g_wantGM == index;
}

struct Title
{
	FSTVolume* vol;
	std::string top;          // "<title id>_v<version>"
};

struct Titles
{
	std::vector<Title> list;  // the game first, then its update and DLC
	~Titles() { for (auto& t : list) delete t.vol; }
};

// "GM0005000E10ABCD00": the update or DLC of a game (kept in a .wua); the game itself
// is the partition Cemu picks
static bool extraTitle(const std::string& name)
{
	if (name.size() != 18 || name.compare(0, 2, "GM") != 0)
		return false;
	std::string type = name.substr(2, 8);
	for (char& c : type)
		c = (char)toupper((unsigned char)c);
	return type == "0005000E" || type == "0005000C";
}

// The disc's game and the update and DLC partitions it carries. An extra partition
// that can't be opened or holds no title is left out (said on stderr): the game
// itself must open.
static int openTitles(const char* disc, Titles& out)
{
	FSTVolume* v;
	g_gms.clear();
	g_wantGM = -1;
	int rc = openDisc(disc, &v);
	if (rc)
		return rc;
	std::string top;
	if (!titleFolder(v, top))
	{
		fprintf(stderr, "wuatool: no readable code/app.xml: not a game disc\n");
		delete v;
		return E_DISC;
	}
	out.list.push_back({v, top});
	std::vector<std::pair<uint32, std::string>> gms = g_gms;
	for (size_t i = 1; i < gms.size(); i++)             // gms[0] is the game (the first GM partition)
	{
		if (!extraTitle(gms[i].second))
			continue;
		g_wantGM = (int)gms[i].first;
		FSTVolume* x = nullptr;
		FSTVolume::ErrorCode ec;
		x = FSTVolume::OpenFromDiscImage(fs::path(disc), &ec);
		g_wantGM = -1;
		std::string t;
		if (!x || !titleFolder(x, t) || std::any_of(out.list.begin(), out.list.end(), [&](auto& o) { return o.top == t; }))
		{
			fprintf(stderr, "wuatool: left out partition %s (can't be read as a title)\n", gms[i].second.c_str());
			delete x;
			continue;
		}
		out.list.push_back({x, t});
	}
	return E_OK;
}

static uint64 titlesSize(Titles& ts, uint32& files)
{
	uint64 total = 0;
	files = 0;
	for (auto& t : ts.list)
	{
		uint32 n;
		total += gameSize(t.vol, n);
		files += n;
	}
	return total;
}

// ------------------------------------------------------------------ pack

struct Out
{
	FILE* f = nullptr;
	bool failed = false;
};
static void cbNewFile(const int32_t, void*) {}
static void cbWrite(const void* d, size_t n, void* ctx)
{
	Out* o = (Out*)ctx;
	if (!o->failed && fwrite(d, 1, n, o->f) != n)
		o->failed = true;
}

static int cmdPack(const char* disc, const char* out)
{
	Titles ts;
	int rc = openTitles(disc, ts);
	if (rc)
		return rc;
	Out o;
	o.f = fopen(out, "wb");
	if (!o.f)
	{
		fprintf(stderr, "wuatool: can't write %s (%s)\n", out, strerror(errno));
		return E_WRITE;
	}
	Progress pr;
	pr.what = "packing";
	uint32 files = 0, count;
	pr.total = titlesSize(ts, count);
	ZArchiveWriter zw(cbNewFile, cbWrite, &o);
	std::vector<uint8> buf(1 << 20);
	int fail = 0;
	bool ok = true;
	for (auto& t : ts.list)
	{
		FSTVolume* v = t.vol;
		std::string top = t.top;
		zw.MakeDir(top.c_str(), false);
		Walker w{v};
		w.onDir = [&](const std::string& p) {
			if (!zw.MakeDir((top + "/" + p).c_str(), false))
			{
				fprintf(stderr, "wuatool: duplicate folder on the disc: %s\n", p.c_str());
				fail = E_DISC;
				return false;
			}
			return true;
		};
		w.onFile = [&](const std::string& p, FSTFileHandle& h) {
			uint32 size = v->GetFileSize(h), off = 0;
			if (!zw.StartNewFile((top + "/" + p).c_str()))
			{
				fprintf(stderr, "wuatool: duplicate file on the disc: %s\n", p.c_str());
				fail = E_DISC;
				return false;
			}
			while (off < size)
			{
				uint32 want = std::min<uint32>((uint32)buf.size(), size - off);
				uint32 n = v->ReadFile(h, off, want, buf.data());
				if (n != want || v->HasCorruption())
				{
					fprintf(stderr, "wuatool: read error in %s/%s at %u (damaged or cut-short image)\n", top.c_str(),
							p.c_str(), off);
					fail = E_READ;
					return false;
				}
				zw.AppendData(buf.data(), n);
				off += n;
				pr.add(n);
				if (o.failed)
				{
					fail = E_WRITE;
					return false;
				}
			}
			files++;
			return true;
		};
		if (!(ok = w.walk("")) || fail)
			break;
	}
	if (ok && !fail)
		zw.Finalize();
	if (fflush(o.f) != 0 || ferror(o.f))
		o.failed = true;
	if (fclose(o.f) != 0)
		o.failed = true;
	if (!ok || fail || o.failed)
	{
		if (o.failed && !fail)
			fprintf(stderr, "wuatool: writing %s failed (disk full?)\n", out);
		remove(out);
		return fail ? fail : (o.failed ? E_WRITE : E_DISC);
	}
	std::string names;
	for (auto& t : ts.list)
		names += (names.empty() ? "" : ", ") + t.top;
	printf("packed %u files, %llu bytes, as %s\n", files, (unsigned long long)pr.done, names.c_str());
	return E_OK;
}

// ------------------------------------------------------------------ the archive

// ZArchive's footer hash: SHA-256 of the whole file with the hash field zeroed
// (ZArchive's reader never checks it)
static int archiveIntegrity(const char* path)
{
	FILE* f = fopen(path, "rb");
	if (!f)
	{
		fprintf(stderr, "wuatool: can't read %s\n", path);
		return E_ARCHIVE;
	}
	const size_t fsz = 144;          // the serialized footer (big-endian, packed)
	fseeko(f, 0, SEEK_END);
	off_t sz = ftello(f);
	if (sz < (off_t)fsz)
	{
		fclose(f);
		fprintf(stderr, "wuatool: too short to be a ZArchive\n");
		return E_ARCHIVE;
	}
	std::vector<uint8> foot(fsz);
	fseeko(f, sz - fsz, SEEK_SET);
	if (fread(foot.data(), 1, fsz, f) != fsz || be32(foot.data() + 140) != 0x169f52d6 ||
		be32(foot.data() + 136) != 0x61bf3a01)
	{
		fclose(f);
		fprintf(stderr, "wuatool: not a ZArchive (no footer)\n");
		return E_ARCHIVE;
	}
	uint64 total = ((uint64)be32(foot.data() + 128) << 32) | be32(foot.data() + 132);
	if (total != (uint64)sz)
	{
		fclose(f);
		fprintf(stderr, "wuatool: archive cut short (%lld of %llu bytes)\n", (long long)sz, (unsigned long long)total);
		return E_ARCHIVE;
	}
	EVP_MD_CTX* c = EVP_MD_CTX_new();
	EVP_DigestInit_ex(c, EVP_sha256(), nullptr);
	std::vector<uint8> b(1 << 20);
	uint64 left = (uint64)sz - fsz;
	fseeko(f, 0, SEEK_SET);
	while (left)
	{
		size_t n = fread(b.data(), 1, (size_t)std::min<uint64>(b.size(), left), f);
		if (!n)
			break;
		EVP_DigestUpdate(c, b.data(), n);
		left -= n;
	}
	fclose(f);
	uint8 stored[32], h[32];
	unsigned hl = 0;
	memcpy(stored, foot.data() + 96, 32);
	memset(foot.data() + 96, 0, 32);
	EVP_DigestUpdate(c, foot.data(), fsz);
	EVP_DigestFinal_ex(c, h, &hl);
	EVP_MD_CTX_free(c);
	if (left || memcmp(h, stored, 32))
	{
		fprintf(stderr, "wuatool: archive damaged (its SHA-256 doesn't match)\n");
		return E_ARCHIVE;
	}
	return E_OK;
}

// every file in the archive: (path, size)
static void listArchive(ZArchiveReader* zr, const std::string& dir, std::vector<std::pair<std::string, uint64>>& out)
{
	ZArchiveNodeHandle dh = zr->LookUp(dir, false, true);
	if (dh == ZARCHIVE_INVALID_NODE)
		return;
	uint32 c = zr->GetDirEntryCount(dh);
	for (uint32 i = 0; i < c; i++)
	{
		ZArchiveReader::DirEntry e;
		if (!zr->GetDirEntry(dh, i, e))
			continue;
		std::string sub = dir.empty() ? std::string(e.name) : dir + "/" + std::string(e.name);
		if (e.isFile)
		{
			ZArchiveNodeHandle fh = zr->LookUp(sub, true, false);
			out.emplace_back(sub, fh == ZARCHIVE_INVALID_NODE ? 0 : zr->GetFileSize(fh));
		}
		else
			listArchive(zr, sub, out);
	}
}

static int cmdVerify(const char* disc, const char* wua)
{
	int rc = archiveIntegrity(wua);
	if (rc)
		return rc;
	Titles ts;
	rc = openTitles(disc, ts);
	if (rc)
		return rc;
	ZArchiveReader* zr = ZArchiveReader::OpenFromFile(wua);
	if (!zr)
	{
		fprintf(stderr, "wuatool: ZArchive can't open %s\n", wua);
		return E_ARCHIVE;
	}
	Progress pr;
	pr.what = "verifying";
	uint32 count;
	pr.total = titlesSize(ts, count);
	std::vector<uint8> a(1 << 20), b(1 << 20);
	uint32 files = 0;
	int fail = 0;
	bool ok = true;
	for (auto& t : ts.list)
	{
		FSTVolume* v = t.vol;
		std::string top = t.top;
		Walker w{v};
		w.onFile = [&](const std::string& p, FSTFileHandle& h) {
			ZArchiveNodeHandle zh = zr->LookUp(top + "/" + p, true, false);
			uint32 size = v->GetFileSize(h);
			if (zh == ZARCHIVE_INVALID_NODE || zr->GetFileSize(zh) != size)
			{
				fprintf(stderr, "wuatool: %s: %s/%s\n", zh == ZARCHIVE_INVALID_NODE ? "missing from the archive" :
						"size differs", top.c_str(), p.c_str());
				fail = E_DIFFERS;
				return false;
			}
			uint32 off = 0;
			while (off < size)
			{
				uint32 want = std::min<uint32>((uint32)a.size(), size - off);
				uint32 n = v->ReadFile(h, off, want, a.data());
				if (n != want || v->HasCorruption())
				{
					fprintf(stderr, "wuatool: read error on the disc in %s/%s at %u\n", top.c_str(), p.c_str(), off);
					fail = E_READ;
					return false;
				}
				if (zr->ReadFromFile(zh, off, n, b.data()) != n || memcmp(a.data(), b.data(), n))
				{
					fprintf(stderr, "wuatool: differs from the disc: %s/%s at %u\n", top.c_str(), p.c_str(), off);
					fail = E_DIFFERS;
					return false;
				}
				off += n;
				pr.add(n);
			}
			files++;
			return true;
		};
		if (!(ok = w.walk("")) || fail)
			break;
	}
	if (!ok || fail)
	{
		delete zr;
		return fail ? fail : E_DISC;
	}
	std::vector<std::pair<std::string, uint64>> inArchive;
	listArchive(zr, "", inArchive);
	ZArchiveNodeHandle root = zr->LookUp("", false, true);
	uint32 tops = root == ZARCHIVE_INVALID_NODE ? 0 : zr->GetDirEntryCount(root);
	delete zr;
	if (inArchive.size() != files || tops != ts.list.size())
	{
		fprintf(stderr, "wuatool: the archive holds %zu files in %u titles, the disc %u in %zu\n", inArchive.size(),
				tops, files, ts.list.size());
		return E_DIFFERS;
	}
	printf("verified %u files, %llu bytes, %zu title%s, identical to the disc\n", files,
		   (unsigned long long)pr.done, ts.list.size(), ts.list.size() == 1 ? "" : "s");
	return E_OK;
}

static int cmdCheck(const char* wua)
{
	int rc = archiveIntegrity(wua);
	if (rc)
		return rc;
	ZArchiveReader* zr = ZArchiveReader::OpenFromFile(wua);
	if (!zr)
	{
		fprintf(stderr, "wuatool: ZArchive can't open %s\n", wua);
		return E_ARCHIVE;
	}
	std::vector<std::pair<std::string, uint64>> files;
	listArchive(zr, "", files);
	std::vector<uint8> b(1 << 20);
	uint64 total = 0;
	for (auto& f : files)
		total += f.second;
	Progress pr;
	pr.what = "checking";
	pr.total = total;
	for (auto& f : files)
	{
		ZArchiveNodeHandle zh = zr->LookUp(f.first, true, false);
		for (uint64 off = 0; off < f.second;)
		{
			uint64 want = std::min<uint64>(b.size(), f.second - off);
			if (zh == ZARCHIVE_INVALID_NODE || zr->ReadFromFile(zh, off, want, b.data()) != want)
			{
				fprintf(stderr, "wuatool: can't read %s from the archive\n", f.first.c_str());
				delete zr;
				return E_ARCHIVE;
			}
			off += want;
			pr.add(want);
		}
	}
	ZArchiveNodeHandle root = zr->LookUp("", false, true);
	uint32 n = root == ZARCHIVE_INVALID_NODE ? 0 : zr->GetDirEntryCount(root);
	for (uint32 i = 0; i < n; i++)
	{
		ZArchiveReader::DirEntry e;
		if (zr->GetDirEntry(root, i, e) && !e.isFile)
			printf("title %.*s\n", (int)e.name.size(), e.name.data());
	}
	delete zr;
	printf("checked %zu files, %llu bytes\n", files.size(), (unsigned long long)total);
	return E_OK;
}

// one file of the archive to stdout ("<title folder>/code/app.xml"), for tests
static int cmdCat(const char* wua, const char* path)
{
	ZArchiveReader* zr = ZArchiveReader::OpenFromFile(wua);
	if (!zr)
	{
		fprintf(stderr, "wuatool: ZArchive can't open %s\n", wua);
		return E_ARCHIVE;
	}
	ZArchiveNodeHandle zh = zr->LookUp(path, true, false);
	if (zh == ZARCHIVE_INVALID_NODE)
	{
		fprintf(stderr, "wuatool: no file %s in the archive\n", path);
		delete zr;
		return E_ARCHIVE;
	}
	std::vector<uint8> b(1 << 20);
	uint64 size = zr->GetFileSize(zh);
	for (uint64 off = 0; off < size;)
	{
		uint64 want = std::min<uint64>(b.size(), size - off);
		if (zr->ReadFromFile(zh, off, want, b.data()) != want || fwrite(b.data(), 1, want, stdout) != want)
		{
			delete zr;
			return E_ARCHIVE;
		}
		off += want;
	}
	delete zr;
	return fflush(stdout) == 0 ? E_OK : E_WRITE;
}

static int cmdProbe(const char* disc)
{
	Titles ts;
	int rc = openTitles(disc, ts);
	if (rc)
		return rc;
	uint32 files;
	uint64 size = titlesSize(ts, files);
	for (auto& t : ts.list)
		printf("title %s\n", t.top.c_str());
	printf("files %u\nbytes %llu\n", files, (unsigned long long)size);
	return E_OK;
}

// ------------------------------------------------------------------ .wud <-> .wux
//
// .wux: the lossless container Cemu reads .wud images from (Cemu's wud.cpp; the
// format Exzap's WudCompress introduced). Its reference writer has no license,
// so this is our own, written to Cemu's reader:
//   0x00 "WUX0", 0x04 0x1099D02E, 0x08 sector size (0x8000), 0x0C 0,
//   0x10 the .wud's size (uint64), 0x18 flags (0), 0x1C 0   (little-endian)
//   0x20 one uint32 per sector of the .wud: its index among the unique sectors
//   then, from the next multiple of the sector size, each unique sector once,
//   in order of first appearance (the last, partial sector padded with zeros).
// A sector is a duplicate only when its bytes equal a stored sector's: a fast
// 64-bit hash picks candidates and each candidate is compared byte for byte,
// so a hash collision costs a comparison, never a wrong sector. Memory: the
// index (4 bytes a sector) and the hash table (one entry per unique sector).

static const uint32 WUX_SECTOR = 0x8000;
static const uint64 WUX_MAX_SIZE = 1ull << 40;   // far above any disc (25 GB)

static uint64 sectorHash(const uint8* p, size_t n)
{
	uint64 h = 0x9E3779B97F4A7C15ull ^ n;
	for (size_t i = 0; i + 8 <= n; i += 8)
	{
		uint64 w;
		memcpy(&w, p + i, 8);
		h ^= w * 0xC2B2AE3D27D4EB4Full;
		h = ((h << 31) | (h >> 33)) * 0x9E3779B97F4A7C15ull;
	}
	return h ^ (h >> 29);
}

static bool readFull(int fd, uint8* p, size_t n, uint64 off)
{
	while (n)
	{
		ssize_t r = pread(fd, p, n, (off_t)off);
		if (r <= 0)
			return false;
		p += r; n -= (size_t)r; off += (uint64)r;
	}
	return true;
}

static bool writeFull(int fd, const uint8* p, size_t n, uint64 off)
{
	while (n)
	{
		ssize_t r = pwrite(fd, p, n, (off_t)off);
		if (r <= 0)
			return false;
		p += r; n -= (size_t)r; off += (uint64)r;
	}
	return true;
}

// A .wux's layout must be whole before Cemu's reader (which checks none of it)
// is trusted with it: a known sector size, a sane size, the whole index, and
// every index pointing at a sector the file holds. Returns E_OK, or E_ARCHIVE
// with the reason printed. Not a .wux at all: E_OK with *isWux false.
static int wuxLayout(const char* path, bool* isWux)
{
	*isWux = false;
	int fd = open(path, O_RDONLY);
	if (fd < 0)
	{
		fprintf(stderr, "wuatool: can't read %s (%s)\n", path, strerror(errno));
		return E_DISC;
	}
	struct stat st;
	uint8 h[0x20];
	if (fstat(fd, &st) != 0 || !readFull(fd, h, sizeof(h), 0) || memcmp(h, "WUX0", 4) ||
		(h[4] | h[5] << 8 | h[6] << 16 | (uint32)h[7] << 24) != 0x1099D02E)
	{
		close(fd);
		return E_OK;
	}
	*isWux = true;
	uint32 sector;
	uint64 size;
	memcpy(&sector, h + 8, 4);
	memcpy(&size, h + 0x10, 8);
	const char* bad = nullptr;
	if (sector < 0x100 || sector >= 0x10000000 || (sector & (sector - 1)))
		bad = "its sector size isn't one Cemu reads";
	else if (size == 0 || size > WUX_MAX_SIZE)
		bad = "its disc size is impossible";
	uint64 count = bad ? 0 : (size + sector - 1) / sector;
	uint64 data = (0x20 + count * 4 + sector - 1) / sector * sector;
	if (!bad && (uint64)st.st_size < data)
		bad = "cut short (its sector index isn't whole)";
	std::vector<uint32> index;
	if (!bad)
	{
		index.resize(count);
		if (!readFull(fd, (uint8*)index.data(), count * 4, 0x20))
			bad = "its sector index can't be read";
	}
	close(fd);
	uint64 held = bad ? 0 : ((uint64)st.st_size - data) / sector;     // whole sectors in the file
	uint64 tail = bad ? 0 : ((uint64)st.st_size - data) % sector;
	for (uint64 i = 0; !bad && i < count; i++)
	{
		uint64 need = i + 1 < count ? sector : size - i * sector;          // the last one may be partial
		if (index[i] >= held && !(index[i] == held && tail >= need))
			bad = "cut short or damaged (a sector index points past the end)";
	}
	if (bad)
	{
		fprintf(stderr, "wuatool: damaged .wux: %s\n", bad);
		return E_ARCHIVE;
	}
	return E_OK;
}

static int cmdWux(const char* src, const char* out)
{
	bool isWux;
	int rc = wuxLayout(src, &isWux);
	if (rc)
		return rc;
	if (isWux)
	{
		fprintf(stderr, "wuatool: %s is a .wux already\n", src);
		return E_DISC;
	}
	int in = open(src, O_RDONLY);
	struct stat st;
	uint8 magic[4]{};
	if (in < 0 || fstat(in, &st) != 0)
	{
		fprintf(stderr, "wuatool: can't read %s (%s)\n", src, strerror(errno));
		return E_DISC;
	}
	uint64 size = (uint64)st.st_size;
	if (size > WUX_MAX_SIZE || !readFull(in, magic, 4, 0x10000) || be32(magic) != 0xCC549EB9)
	{
		close(in);
		fprintf(stderr, "wuatool: not a Wii U disc image\n");
		return E_DISC;
	}
	int o = open(out, O_RDWR | O_CREAT | O_TRUNC, 0644);    // read back to compare candidates
	if (o < 0)
	{
		close(in);
		fprintf(stderr, "wuatool: can't write %s (%s)\n", out, strerror(errno));
		return E_WRITE;
	}
	uint64 count = (size + WUX_SECTOR - 1) / WUX_SECTOR;
	uint64 data = (0x20 + count * 4 + WUX_SECTOR - 1) / WUX_SECTOR * WUX_SECTOR;
	std::vector<uint32> index(count);
	std::unordered_multimap<uint64, uint32> seen;
	std::vector<uint8> buf(64 * WUX_SECTOR), cmp(WUX_SECTOR);
	uint32 unique = 0;
	int fail = 0;
	Progress pr;
	pr.what = "compressing";
	pr.total = size;
	for (uint64 i = 0; i < count && !fail;)
	{
		uint64 at = i * WUX_SECTOR, n = std::min<uint64>(buf.size(), size - at);
		if (!readFull(in, buf.data(), n, at))
		{
			fprintf(stderr, "wuatool: read error at %llu (the image changed or is unreadable)\n", (unsigned long long)at);
			fail = E_READ;
			break;
		}
		if (n % WUX_SECTOR)
			memset(buf.data() + n, 0, WUX_SECTOR - n % WUX_SECTOR);
		for (uint64 k = 0; k * WUX_SECTOR < n && !fail; k++, i++)
		{
			const uint8* s = buf.data() + k * WUX_SECTOR;
			uint64 hv = sectorHash(s, WUX_SECTOR);
			uint32 found = 0xFFFFFFFF;
			auto range = seen.equal_range(hv);
			for (auto it = range.first; it != range.second && found == 0xFFFFFFFF; ++it)
			{
				if (!readFull(o, cmp.data(), WUX_SECTOR, data + (uint64)it->second * WUX_SECTOR))
				{
					fail = E_WRITE;     // our own output can't be read back
					break;
				}
				if (!memcmp(cmp.data(), s, WUX_SECTOR))
					found = it->second;
			}
			if (found == 0xFFFFFFFF && !fail)
			{
				if (!writeFull(o, s, WUX_SECTOR, data + (uint64)unique * WUX_SECTOR))
					fail = E_WRITE;
				seen.emplace(hv, unique);
				found = unique++;
			}
			index[i] = found;
		}
		pr.add(n);
	}
	uint8 h[0x20]{};
	memcpy(h, "WUX0", 4);
	uint32 m1 = 0x1099D02E, sec = WUX_SECTOR;
	memcpy(h + 4, &m1, 4);
	memcpy(h + 8, &sec, 4);
	memcpy(h + 0x10, &size, 8);
	std::vector<uint8> head(data, 0);
	memcpy(head.data(), h, sizeof(h));
	memcpy(head.data() + 0x20, index.data(), count * 4);
	struct stat after;
	if (!fail && (fstat(in, &after) != 0 || (uint64)after.st_size != size))
	{
		fprintf(stderr, "wuatool: the image changed size while it was read\n");
		fail = E_READ;
	}
	if (!fail && (!writeFull(o, head.data(), head.size(), 0) || fsync(o) != 0))
		fail = E_WRITE;
	close(in);
	if (close(o) != 0 && !fail)
		fail = E_WRITE;
	if (fail)
	{
		if (fail == E_WRITE)
			fprintf(stderr, "wuatool: writing %s failed (disk full?)\n", out);
		remove(out);
		return fail;
	}
	printf("compressed %llu sectors to %u unique, %llu bytes as %llu\n", (unsigned long long)count, unique,
		   (unsigned long long)size, (unsigned long long)(data + (uint64)unique * WUX_SECTOR));
	return E_OK;
}

// The disc image's bytes as Cemu reads them (a .wux through its index, checked
// first), with their CRC32, MD5 and SHA-1, the hashes RomM keeps of a .wud.
struct Image
{
	wud_t* wud = nullptr;
	uint64 size = 0;
	~Image() { if (wud) wud_close(wud); }
	int open(const char* path)
	{
		bool isWux;
		int rc = wuxLayout(path, &isWux);
		if (rc)
			return rc;
		wud = wud_open(fs::path(path));
		if (!wud)
		{
			fprintf(stderr, "wuatool: can't open %s\n", path);
			return E_DISC;
		}
		size = (uint64)wud_getWUDSize(wud);
		return E_OK;
	}
	bool read(uint8* p, uint32 n, uint64 off) { return wud_readData(wud, p, n, (long long)off) == n; }
};

struct Hashes
{
	uint32 crc = 0xFFFFFFFF, table[256];
	EVP_MD_CTX* md5 = EVP_MD_CTX_new();
	EVP_MD_CTX* sha1 = EVP_MD_CTX_new();
	Hashes()
	{
		for (uint32 i = 0; i < 256; i++)
		{
			uint32 c = i;
			for (int k = 0; k < 8; k++)
				c = c & 1 ? 0xEDB88320 ^ (c >> 1) : c >> 1;
			table[i] = c;
		}
		EVP_DigestInit_ex(md5, EVP_md5(), nullptr);
		EVP_DigestInit_ex(sha1, EVP_sha1(), nullptr);
	}
	~Hashes() { EVP_MD_CTX_free(md5); EVP_MD_CTX_free(sha1); }
	void add(const uint8* p, size_t n)
	{
		for (size_t i = 0; i < n; i++)
			crc = table[(crc ^ p[i]) & 0xFF] ^ (crc >> 8);
		EVP_DigestUpdate(md5, p, n);
		EVP_DigestUpdate(sha1, p, n);
	}
	void print(uint64 size)
	{
		uint8 m[16], s[20];
		unsigned l;
		EVP_DigestFinal_ex(md5, m, &l);
		EVP_DigestFinal_ex(sha1, s, &l);
		printf("size %llu\ncrc32 %08x\nmd5 ", (unsigned long long)size, crc ^ 0xFFFFFFFF);
		for (uint8 b : m)
			printf("%02x", b);
		printf("\nsha1 ");
		for (uint8 b : s)
			printf("%02x", b);
		printf("\n");
	}
};

// hash: the image's size and hashes. compare: two images (any mix of .wud and
// .wux) must hold the same bytes; prints the first one's hashes. unwux: the
// image written out as a plain .wud.
static int cmdImage(const char* cmd, const char* a, const char* b)
{
	Image x, y;
	int rc = x.open(a);
	if (rc)
		return rc;
	bool compare = !strcmp(cmd, "compare"), unwux = !strcmp(cmd, "unwux");
	if (compare && (rc = y.open(b)))
		return rc;
	if (compare && x.size != y.size)
	{
		fprintf(stderr, "wuatool: the images differ in size (%llu vs %llu bytes)\n", (unsigned long long)x.size,
				(unsigned long long)y.size);
		return E_DIFFERS;
	}
	FILE* out = nullptr;
	if (unwux && !(out = fopen(b, "wb")))
	{
		fprintf(stderr, "wuatool: can't write %s (%s)\n", b, strerror(errno));
		return E_WRITE;
	}
	Hashes hs;
	Progress pr;
	pr.what = compare ? "comparing" : unwux ? "expanding" : "hashing";
	pr.total = x.size;
	std::vector<uint8> p(1 << 20), q(1 << 20);
	int fail = 0;
	for (uint64 off = 0; off < x.size && !fail;)
	{
		uint32 n = (uint32)std::min<uint64>(p.size(), x.size - off);
		if (!x.read(p.data(), n, off) || (compare && !y.read(q.data(), n, off)))
		{
			fprintf(stderr, "wuatool: read error at %llu (cut short?)\n", (unsigned long long)off);
			fail = E_READ;
		}
		else if (compare && memcmp(p.data(), q.data(), n))
		{
			fprintf(stderr, "wuatool: the images differ near byte %llu\n", (unsigned long long)off);
			fail = E_DIFFERS;
		}
		else if (out && fwrite(p.data(), 1, n, out) != n)
			fail = E_WRITE;
		else
		{
			hs.add(p.data(), n);
			off += n;
			pr.add(n);
		}
	}
	if (out && (fclose(out) != 0) && !fail)
		fail = E_WRITE;
	if (fail)
	{
		if (out)
			remove(b);
		if (fail == E_WRITE)
			fprintf(stderr, "wuatool: writing %s failed (disk full?)\n", b);
		return fail;
	}
	hs.print(x.size);
	return E_OK;
}

// ------------------------------------------------------------------ main

static int usage()
{
	fprintf(stderr,
			"wuatool %s: Wii U disc image (.wud/.wux) -> .wua, for Cemu\n"
			"usage: wuatool pack   [-k keys]... <disc.wud|wux> <out.wua>\n"
			"       wuatool verify [-k keys]... <disc.wud|wux> <game.wua>\n"
			"       wuatool probe  [-k keys]... <disc.wud|wux>\n"
			"       wuatool check  <game.wua>\n"
			"       wuatool cat    <game.wua> <path in it>\n"
			"       wuatool wux    <disc.wud> <out.wux>   (lossless; no keys needed)\n"
			"       wuatool unwux  <disc.wux> <out.wud>\n"
			"       wuatool compare <disc.wud|wux> <disc.wud|wux>\n"
			"       wuatool hash   <disc.wud|wux>\n"
			"-k: disc keys in Cemu's keys.txt format (or a 16-byte key file).\n",
			WUATOOL_VERSION);
	return E_USAGE;
}

int main(int argc, char** argv)
{
	AES128_init();
	if (argc == 2 && (!strcmp(argv[1], "--version") || !strcmp(argv[1], "-V")))
	{
		printf("wuatool %s\n", WUATOOL_VERSION);
		return E_OK;
	}
	if (argc < 3)
		return usage();
	std::string cmd = argv[1];
	std::vector<const char*> pos;
	for (int i = 2; i < argc; i++)
	{
		if (!strcmp(argv[i], "-k") && i + 1 < argc)
		{
			if (!loadKeyFile(argv[++i]))
				return E_USAGE;
		}
		else if (!strcmp(argv[i], "-T") && i + 1 < argc)
		{
			if (!loadTestCommonKey(argv[++i]))
				return E_USAGE;
		}
		else
			pos.push_back(argv[i]);
	}
	setvbuf(stderr, nullptr, _IOLBF, 0);
	if (cmd == "pack" && pos.size() == 2)
		return cmdPack(pos[0], pos[1]);
	if (cmd == "verify" && pos.size() == 2)
		return cmdVerify(pos[0], pos[1]);
	if (cmd == "probe" && pos.size() == 1)
		return cmdProbe(pos[0]);
	if (cmd == "check" && pos.size() == 1)
		return cmdCheck(pos[0]);
	if (cmd == "cat" && pos.size() == 2)
		return cmdCat(pos[0], pos[1]);
	if (cmd == "wux" && pos.size() == 2)
		return cmdWux(pos[0], pos[1]);
	if ((cmd == "compare" || cmd == "unwux") && pos.size() == 2)
		return cmdImage(cmd.c_str(), pos[0], pos[1]);
	if (cmd == "hash" && pos.size() == 1)
		return cmdImage("hash", pos[0], nullptr);
	return usage();
}
