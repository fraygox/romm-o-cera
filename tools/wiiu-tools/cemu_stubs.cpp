// The few Cemu functions its disc code (FST, ncrypto) links against that wuatool
// doesn't otherwise need: logging goes to stderr, the console identity (otp.bin,
// seeprom) is absent. Part of wuatool (tools/wiiu-tools/ in RomM-o-Cera), MPL-2.0.
#include "Common/precompiled.h"
#include <cstdio>

bool cemuLog_log(LogType type, std::string_view text)
{
	fprintf(stderr, "[cemu] %.*s\n", (int)text.size(), text.data());
	return true;
}

bool cemuLog_log(LogType type, std::u8string_view text)
{
	fprintf(stderr, "[cemu] %.*s\n", (int)text.size(), (const char*)text.data());
	return true;
}

uint64 s_loggingFlagMask = ~0ull;
bool hasOtpMem = false, hasSeepromMem = false;
bool iosuCrypto_getDeviceId(uint32* id) { return false; }
void iosuCrypto_getDeviceSerialString(char* s) { s[0] = 0; }
void iosuCrypto_readSeepromData(void* o, sint32 w, sint32 s) { memset(o, 0, s); }
void iosuCrypto_getDeviceCertPrivateKey(void* o, sint32 l) { memset(o, 0, l); }
void iosuCrypto_getDeviceCertificate(void* o, sint32 l) { memset(o, 0, l); }
