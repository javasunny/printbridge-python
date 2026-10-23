"""Bounded IPP wire encoding (RFC 8010); document bytes are not interpreted."""
from dataclasses import dataclass
import struct

MAX_ATTRIBUTES_BYTES = 65536
MAX_VALUES = 1024


class IncompleteMessage(ValueError):
    pass

GROUP_TAGS = {1, 2, 4, 5}
STRING_TAGS = {0x41, 0x42, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49}


@dataclass
class Attribute:
    name: str
    tag: int
    values: list


@dataclass
class Group:
    tag: int
    attributes: list[Attribute]


@dataclass
class Message:
    version: tuple[int, int]
    code: int
    request_id: int
    groups: list[Group]
    document_offset: int


def _value(tag, raw):
    if tag in (0x21, 0x23) and len(raw) == 4:
        return struct.unpack('>i', raw)[0]
    if tag == 0x22 and raw in (b'\0', b'\1'):
        return bool(raw[0])
    if tag == 0x33 and len(raw) == 8:
        return struct.unpack('>ii', raw)
    if tag == 0x32 and len(raw) == 9:
        result = struct.unpack('>iiB', raw)
        if result[2] in (3, 4):
            return result
    if tag == 0x31 and len(raw) == 11:
        return raw
    if tag == 0x30:
        return raw
    if tag in STRING_TAGS:
        return raw.decode('utf-8')
    if 0x10 <= tag <= 0x17 and not raw:
        return None
    raise ValueError('Invalid or unsupported IPP value')


def decode(data: bytes) -> Message:
    pos = 0
    count = 0

    def take(size):
        nonlocal pos
        if pos + size > MAX_ATTRIBUTES_BYTES:
            raise ValueError('Oversized IPP attributes')
        if pos + size > len(data):
            raise IncompleteMessage('Truncated IPP attributes')
        result = data[pos:pos + size]
        pos += size
        return result

    def length():
        return struct.unpack('>H', take(2))[0]

    def read_value(tag, raw, depth=0):
        nonlocal count
        if tag != 0x34:
            return _value(tag, raw)
        if raw or depth >= 4:
            raise ValueError('Invalid IPP collection')
        members = []
        pending = None
        while True:
            member_tag = take(1)[0]
            member_name = take(length())
            member_raw = take(length())
            count += 1
            if count > MAX_VALUES or member_name:
                raise ValueError('Invalid IPP collection')
            if member_tag == 0x37:
                if member_raw or pending is not None:
                    raise ValueError('Invalid IPP collection end')
                return members
            if member_tag == 0x4a:
                if pending is not None or len(member_raw) > 255 or (not member_raw and not members):
                    raise ValueError('Invalid IPP collection member')
                pending = member_raw.decode('ascii')
                continue
            value = read_value(member_tag, member_raw, depth + 1)
            if pending:
                members.append(Attribute(pending, member_tag, [value]))
            elif members and members[-1].tag == member_tag:
                members[-1].values.append(value)
            else:
                raise ValueError('Invalid IPP collection member')
            pending = None

    try:
        major, minor, code, request_id = struct.unpack('>BBHi', take(8))
        groups = []
        while True:
            tag = take(1)[0]
            if tag == 3:
                return Message((major, minor), code, request_id, groups, pos)
            if tag in GROUP_TAGS:
                groups.append(Group(tag, []))
                continue
            if not groups or tag < 0x10:
                raise ValueError('Invalid IPP attribute group')
            name = take(length()).decode('ascii')
            raw = take(length())
            count += 1
            if count > MAX_VALUES:
                raise ValueError('Too many IPP attribute values')
            value = read_value(tag, raw)
            attrs = groups[-1].attributes
            if name:
                if len(name) > 255:
                    raise ValueError('Invalid IPP attribute name')
                attrs.append(Attribute(name, tag, [value]))
            elif attrs and attrs[-1].tag == tag:
                attrs[-1].values.append(value)
            else:
                raise ValueError('Invalid IPP additional value')
    except (UnicodeError, struct.error, IndexError):
        raise ValueError('Invalid IPP encoding') from None


def _raw(tag, value):
    if tag in (0x21, 0x23):
        return struct.pack('>i', value)
    if tag == 0x22 and isinstance(value, bool):
        return bytes([value])
    if tag == 0x33:
        return struct.pack('>ii', *value)
    if tag == 0x32:
        return struct.pack('>iiB', *value)
    if tag in (0x30, 0x31) and isinstance(value, bytes):
        return value
    if tag in STRING_TAGS and isinstance(value, str):
        return value.encode('utf-8')
    if 0x10 <= tag <= 0x17 and value is None:
        return b''
    raise ValueError('Invalid IPP value')


def encode(version, code, request_id, groups) -> bytes:
    try:
        out = bytearray(struct.pack('>BBHi', *version, code, request_id))
        count = 0

        def record(tag, name, raw):
            nonlocal count
            count += 1
            if count > MAX_VALUES:
                raise ValueError('Too many IPP attribute values')
            if len(out) + 5 + len(name) + len(raw) + 1 > MAX_ATTRIBUTES_BYTES:
                raise ValueError('Oversized IPP attributes')
            out.extend(struct.pack('>BH', tag, len(name)))
            out.extend(name)
            out.extend(struct.pack('>H', len(raw)))
            out.extend(raw)

        def attributes(attrs, depth=0):
            for attr in attrs:
                name = attr.name.encode('ascii')
                if not name or len(name) > 255 or not attr.values:
                    raise ValueError('Invalid IPP attribute')
                if depth:
                    record(0x4a, b'', name)
                for index, value in enumerate(attr.values):
                    wire_name = name if index == 0 and not depth else b''
                    if attr.tag == 0x34:
                        if depth >= 4 or not isinstance(value, list):
                            raise ValueError('Invalid IPP collection')
                        record(attr.tag, wire_name, b'')
                        attributes(value, depth + 1)
                        record(0x37, b'', b'')
                    else:
                        raw = _raw(attr.tag, value)
                        _value(attr.tag, raw)
                        record(attr.tag, wire_name, raw)

        for group in groups:
            if group.tag not in GROUP_TAGS:
                raise ValueError('Invalid IPP attribute group')
            out.append(group.tag)
            attributes(group.attributes)
        out.append(3)
        if len(out) > MAX_ATTRIBUTES_BYTES:
            raise ValueError('Oversized IPP attributes')
        return bytes(out)
    except (UnicodeError, struct.error, TypeError, OverflowError):
        raise ValueError('Invalid IPP encoding') from None
