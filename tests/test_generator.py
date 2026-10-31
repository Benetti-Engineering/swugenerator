# pylint: disable=C0114,C0116,W0621
"""Tests of the processing of sw-description"""
import json

import libconf
import pytest

from swugenerator.generator import SWUGenerator
from swugenerator.swu_file import SWUFile


@pytest.fixture()
def generator(tmp_path):
    (tmp_path / "image.bin").write_bytes(b"0123456789")
    gen = SWUGenerator(
        tmp_path / "sw-description", tmp_path / "out.swu", {}, [tmp_path], None, None, None
    )
    yield gen
    gen.abort()


@pytest.mark.parametrize(
    "line,expected",
    [
        ('size = "$swupdate_get_size(image.bin)";', 'size = "10";'),
        (
            'sha256 = "$swupdate_get_sha256(image.bin)";',
            'sha256 = "84d89877f0d4041efb6bf91a16f0248f2fd573e6af05c19f96bedb9f882f7882";',
        ),
    ],
)
def test_functions(generator, line, expected):
    generator.lines = [line]
    generator._exec_functions()  # pylint: disable=protected-access
    assert generator.lines == [expected]


@pytest.mark.parametrize("function", ["swupdate_get_size", "swupdate_get_sha256"])
def test_functions_missing_artifact(generator, function):
    generator.lines = [f'value = "${function}(missing.bin)";']
    with pytest.raises(SystemExit):
        generator._exec_functions()  # pylint: disable=protected-access


def build(tmp_path, images, dirs=None, scripts=None, extra=""):
    """Builds a SWU with the images and returns its files and sw-description"""
    (tmp_path / "sw-description").write_text(
        'software = { version = "1.0"; ' + extra
        + "images: ( " + ", ".join(images) + " ); };\n"
    )
    gen = SWUGenerator(
        tmp_path / "sw-description", tmp_path / "out.swu", {}, dirs or [tmp_path],
        None, None, None, embedded_scripts=scripts
    )
    try:
        gen.process()
        gen.close()
    except BaseException:
        gen.abort()
        raise
    outdir = tmp_path / "extracted"
    with open(tmp_path / "out.swu", "rb") as swu:
        files = SWUFile(swu).extract(outdir)
    return files, libconf.loads((outdir / "sw-description").read_text())


@pytest.mark.parametrize("filename", ["sub/image.bin", "../image.bin", "{tmp}/image.bin"])
@pytest.mark.parametrize("attributes", ["", 'compressed = "zlib";'])
def test_filename_with_directory(tmp_path, filename, attributes):
    # all of them exist, seen from the artifactory
    (tmp_path / "art" / "sub").mkdir(parents=True)
    (tmp_path / "art" / "sub" / "image.bin").write_bytes(b"0123456789")
    (tmp_path / "image.bin").write_bytes(b"0123456789")
    filename = filename.format(tmp=tmp_path)

    with pytest.raises(SystemExit):
        build(
            tmp_path, [f'{{ filename = "{filename}"; {attributes} }}'], [tmp_path / "art"]
        )
    assert not (tmp_path / "out.swu").exists()


def test_plain_filename(tmp_path):
    (tmp_path / "image.bin").write_bytes(b"0123456789")
    files, swdesc = build(tmp_path, ['{ filename = "image.bin"; compressed = "zlib"; }'])

    assert files == ["sw-description", "image.bin.zlib"]
    assert swdesc.software.images[0].filename == "image.bin.zlib"


LUA_A = """-- "quotes" and 'single'
function set_version(image)
\tprint("path: C:\\\\dir\\n\\tnext \\"q\\"")
\treturn true, image
end
"""
LUA_B = 'local s = "no newline at the end \\t \u00fc"'


def test_embedded_script(tmp_path):
    (tmp_path / "image.bin").write_bytes(b"0123456789")
    (tmp_path / "a.lua").write_text(LUA_A)
    (tmp_path / "b.lua").write_text(LUA_B)
    _, swdesc = build(
        tmp_path, ['{ filename = "image.bin"; hook = "set_version"; }'],
        scripts=[tmp_path / "a.lua", tmp_path / "b.lua"],
    )
    # joined in order, each file ends with a newline
    assert swdesc.software["embedded-script"] == LUA_A + LUA_B + "\n"
    # newlines and tabs are written as they are, not as \\n and \\t
    text = (tmp_path / "extracted" / "sw-description").read_text()
    assert "function set_version(image)\n\tprint(" in text


def test_embedded_script_json(tmp_path):
    (tmp_path / "image.bin").write_bytes(b"0123456789")
    (tmp_path / "a.lua").write_text(LUA_A)
    (tmp_path / "sw-description").write_text(
        '{ "software": { "version": "1.0", "images": [ { "filename": "image.bin" } ] } }'
    )
    gen = SWUGenerator(
        tmp_path / "sw-description", tmp_path / "out.swu", {}, [tmp_path],
        None, None, None, embedded_scripts=[tmp_path / "a.lua"]
    )
    gen.process()
    gen.close()
    with open(tmp_path / "out.swu", "rb") as swu:
        SWUFile(swu).extract(tmp_path / "extracted")
    swdesc = json.loads((tmp_path / "extracted" / "sw-description").read_text())
    assert swdesc["software"]["embedded-script"] == LUA_A


def test_escaped_backslash_in_template(tmp_path):
    # an escaped backslash before n or t is kept, \t after it is a tab
    (tmp_path / "image.bin").write_bytes(b"0123456789")
    _, swdesc = build(
        tmp_path, ['{ filename = "image.bin"; }'],
        extra=r'embedded-script = "print(\"a\\nb\\\\\tc\")";',
    )
    assert swdesc.software["embedded-script"] == r'print("a\nb\\' + "\t" + r'c")'


@pytest.mark.parametrize(
    "extra", ['embedded-script = "x";', 'board = { embedded-script = "x"; };']
)
def test_embedded_script_already_in_template(tmp_path, extra):
    (tmp_path / "image.bin").write_bytes(b"0123456789")
    (tmp_path / "a.lua").write_text(LUA_A)
    with pytest.raises(SystemExit):
        build(tmp_path, ['{ filename = "image.bin"; }'],
              scripts=[tmp_path / "a.lua"], extra=extra)
    assert not (tmp_path / "out.swu").exists()


def test_embedded_script_missing(tmp_path):
    (tmp_path / "image.bin").write_bytes(b"0123456789")
    with pytest.raises(SystemExit):
        build(tmp_path, ['{ filename = "image.bin"; }'],
              scripts=[tmp_path / "missing.lua"])
    assert not (tmp_path / "out.swu").exists()
