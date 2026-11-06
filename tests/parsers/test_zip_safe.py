"""All generated archive payloads are synthetic and stay under tmp_path."""

import stat
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from datumoj.rikolto.core.contracts import ParseError
from datumoj.rikolto.parsers._zip import MAX_FILE_BYTES, MAX_MEMBERS, MAX_UNCOMPRESSED_BYTES, read_zip


class RawName(ZipInfo):
    def __init__(self, raw, flags=0):
        super().__init__(raw.decode("cp437"))
        self.raw_name, self.flags = raw, flags

    def _encodeFilenameFlags(self):
        return self.raw_name, self.flags


def archive(tmp_path, entries):
    path = tmp_path / "input.synthetic.zip"
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as z:
        for name, content in entries:
            z.writestr(name, content)
    return path.read_bytes()


@pytest.mark.parametrize("name", ["/absolute", "../escape", "dir/../../escape", "C:/escape", "C:escape",
                                 "dir\\escape", RawName(b"bad\x00name")])
def test_rejects_unsafe_paths_without_reading_any_payload(tmp_path, monkeypatch, name):
    data = archive(tmp_path, [("safe", b"first"), (name, b"bad")])
    def forbidden(*args, **kwargs):
        pytest.fail("All entries must be validated before any content is read")
    monkeypatch.setattr(ZipFile, "open", forbidden)
    with pytest.raises(ParseError, match="Unsafe"):
        read_zip(data)
    assert [p.name for p in tmp_path.iterdir()] == ["input.synthetic.zip"]


def test_rejects_symlink(tmp_path):
    link = ZipInfo("link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(ParseError, match="symlink"):
        read_zip(archive(tmp_path, [(link, b"../../target")]))


def test_expansion_and_entry_limits_are_atomic(tmp_path):
    data = archive(tmp_path, [("one", b"a" * 10), ("two", b"b" * 11)])
    assert sum(map(len, read_zip(data, max_total_bytes=21, max_members=2).values())) == 21
    with pytest.raises(ParseError, match="size exceeds"):
        read_zip(data, max_total_bytes=20)
    with pytest.raises(ParseError, match="count exceeds"):
        read_zip(data, max_members=1)
    declared = bytearray(data)
    central = declared.index(b"PK\x01\x02")
    declared[central + 24:central + 28] = (MAX_UNCOMPRESSED_BYTES + 1).to_bytes(4, "little")
    with pytest.raises(ParseError, match="size exceeds"):
        read_zip(bytes(declared))
    assert (MAX_FILE_BYTES, MAX_UNCOMPRESSED_BYTES, MAX_MEMBERS) == (26214400, 262144000, 10000)


def test_filename_encodings_and_member_content_are_independent(tmp_path):
    data = archive(tmp_path, [
        (RawName("佐賀.txt".encode("cp932")), "資料".encode("cp932")),
        (RawName("あ.txt".encode()), "資料".encode()),  # cp932 fails, UTF-8 fallback
        ("町村.geojson", b"{}"),  # zipfile sets UTF-8 flag
    ])
    members = read_zip(data)
    assert members == {"佐賀.txt": "資料".encode("cp932"), "あ.txt": "資料".encode(), "町村.geojson": b"{}"}


@pytest.mark.parametrize("name", [RawName(b"\x81"), RawName(b"\xff", 0x800)])
def test_undecodable_names_fail(tmp_path, name):
    with pytest.raises(ParseError, match="Invalid ZIP"):
        read_zip(archive(tmp_path, [(name, b"content")]))


def test_duplicate_normalized_paths_and_corruption_fail(tmp_path):
    with pytest.raises(ParseError, match="Duplicate"):
        read_zip(archive(tmp_path, [("dir/file", b"a"), ("dir/./file", b"b")]))
    with pytest.raises(ParseError):
        read_zip(b"not a zip")
    data = archive(tmp_path, [("ok", b"a")])
    with pytest.raises(ParseError):
        read_zip(data[:20])
    with pytest.raises(ParseError, match="25 MiB"):
        read_zip(b"a" * (MAX_FILE_BYTES + 1))
