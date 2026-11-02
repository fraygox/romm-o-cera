"""Cue sheets: only a line that starts with FILE names a track, and a track
name made on Windows (backslashes, other letter case) resolves to the file."""
import os

import rommlib


def _cue(tmp_path, text, *files):
    for f in files:
        p = tmp_path / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    cue = tmp_path / "G.cue"
    cue.write_text(text)
    return str(cue)


def test_file_word_outside_a_file_line_is_not_a_track(tmp_path):
    cue = _cue(tmp_path, 'REM COMMENT "Pro File Manager"\nTITLE "Pro File Manager"\n'
               'PERFORMER "A file named x"\n  FILE "a.bin" BINARY\r\nFILE b.bin BINARY\n', "a.bin", "b.bin")
    assert [os.path.basename(p) for p in rommlib.sheet_sources(cue)] == ["a.bin", "b.bin"]


def test_cue_backslashes_and_case_resolve_to_the_file(tmp_path):
    cue = _cue(tmp_path, 'FILE "Sub\\TRACK 01.BIN" BINARY\nFILE Other.BIN BINARY\nFILE missing.bin BINARY\n', "sub/Track 01.bin", "other.bin")
    got = rommlib.sheet_sources(cue)
    assert got[0] == str(tmp_path / "sub" / "Track 01.bin")
    assert got[1] == str(tmp_path / "other.bin")
    assert got[2] == str(tmp_path / "missing.bin")
