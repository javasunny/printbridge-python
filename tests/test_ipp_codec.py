import struct
import pytest
from printbridge.ipp_codec import Attribute, Group, decode, encode


def test_roundtrip_and_document_offset():
    groups = [Group(1, [Attribute('attributes-charset', 0x47, ['utf-8']),
                        Attribute('requested-attributes', 0x44, ['all', 'job-id'])]),
              Group(2, [Attribute('copies', 0x21, [2]),
                        Attribute('job-state', 0x23, [3]),
                        Attribute('flag', 0x22, [True]),
                        Attribute('range', 0x33, [(1, 10)]),
                        Attribute('resolution', 0x32, [(300, 300, 3)]),
                        Attribute('date', 0x31, [bytes(11)]),
                        Attribute('bytes', 0x30, [b'\x00\xff']),
                        Attribute('job-name', 0x42, ['中文文档'])])]
    wire = encode((1, 1), 2, 42, groups)
    message = decode(wire + b'%PDF-document')
    assert message.groups == groups
    assert (message.version, message.code, message.request_id) == ((1, 1), 2, 42)
    assert message.document_offset == len(wire)


def test_every_truncation_is_rejected():
    wire = encode((2, 0), 2, 1, [Group(1, [Attribute('copies', 0x21, [1, 2])])])
    for end in range(len(wire)):
        with pytest.raises(ValueError):
            decode(wire[:end])


@pytest.mark.parametrize('suffix', [
    b'\x01\x21\x00\x00\x00\x04\x00\x00\x00\x01\x03',
    b'\x01\x21\x00\x01x\x00\x01\x01\x03',
    b'\x01\x22\x00\x01x\x00\x01\x02\x03',
    b'\x06\x03', b'\x01\x34\x00\x01x\x00\x01x\x03',
    b'\x01\x42\xff\xffx',
])
def test_malformed_attributes(suffix):
    with pytest.raises(ValueError):
        decode(struct.pack('>BBHi', 1, 1, 2, 1) + suffix)


def test_limits():
    with pytest.raises(ValueError):
        encode((1, 1), 2, 1, [Group(1, [Attribute('x', 0x30, [b'x' * 65536])])])
    with pytest.raises(ValueError):
        encode((1, 1), 2, 1, [Group(1, [Attribute('x', 0x21, [1] * 1025)])])
    header = struct.pack('>BBHi', 1, 1, 2, 1)
    with pytest.raises(ValueError):
        decode(header + b'\x01' * 65536 + b'\x03')


def test_collection_and_depth_limit():
    members = [Attribute('media-size', 0x34, [[
        Attribute('x-dimension', 0x21, [21000]),
        Attribute('y-dimension', 0x21, [29700]),
    ]]), Attribute('media-type', 0x44, ['stationery', 'plain'])]
    groups = [Group(2, [Attribute('media-col', 0x34, [members])])]
    assert decode(encode((2, 0), 2, 1, groups)).groups == groups
    nested = [Attribute('x', 0x21, [1])]
    for _ in range(5):
        nested = [Attribute('nested', 0x34, [nested])]
    with pytest.raises(ValueError):
        encode((2, 0), 2, 1, [Group(2, nested)])


def test_incomplete_distinct_from_invalid():
    from printbridge.ipp_codec import IncompleteMessage
    with pytest.raises(IncompleteMessage):
        decode(b'\x01')
    with pytest.raises(ValueError) as error:
        decode(struct.pack('>BBHi', 1, 1, 2, 1) + b'\x06')
    assert not isinstance(error.value, IncompleteMessage)
