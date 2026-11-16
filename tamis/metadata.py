"""Titles, captions, keywords and the photographer's credits, written into exported JPEGs.

A photo's own title, caption and keywords (`Photo.metadata`) are kept in the
session and in its XMP sidecar (dc:title, dc:description, dc:subject, see
xmp.py); the credits (creator, copyright, contact) and default keywords come
from the export settings. Exports carry them twice: as an XMP packet (APP1),
which current software reads, and as an IPTC-IIM block (APP13, Photoshop's
image resource 0x0404), for older software.
"""
import struct
from html import escape

FIELDS = ("title", "caption", "keywords")
XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\0"
PHOTOSHOP_HEADER = b"Photoshop 3.0\0"
_SEGMENT_LIMIT = 65533  # payload of one JPEG segment

# IIM datasets of record 2, with their maximum length in bytes.
_IIM = {
    "title": (5, 64),  # Object Name
    "keywords": (25, 64),
    "creator": (80, 32),  # By-line
    "copyright": (116, 128),
    "contact": (118, 128),
    "caption": (120, 2000),
}


def clean(value):
    """A photo's metadata as stored: {"title", "caption", "keywords"} without what is empty; {} if nothing."""
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in ("title", "caption"):
        text = value.get(key)
        if isinstance(text, str) and text.strip():
            result[key] = text.strip()
    keywords = value.get("keywords")
    if isinstance(keywords, (list, tuple)):
        words = list(dict.fromkeys(word.strip() for word in keywords if isinstance(word, str) and word.strip()))
        if words:
            result["keywords"] = words
    return result


def for_export(settings, photo_metadata):
    """What an export carries: the photo's title, caption and keywords with the credits and keywords of `settings`."""
    data = dict(clean(photo_metadata or {}))
    if settings is not None:
        data.update(settings.credits)
        keywords = list(dict.fromkeys([*data.get("keywords", []), *settings.keyword_list()]))
        if keywords:
            data["keywords"] = keywords
    return data


# -- XMP -----------------------------------------------------------------------

def _alt(tag, text):
    return f'   <{tag}><rdf:Alt><rdf:li xml:lang="x-default">{escape(text)}</rdf:li></rdf:Alt></{tag}>\n'


def _list(tag, kind, items):
    lines = "".join(f"     <rdf:li>{escape(item)}</rdf:li>\n" for item in items)
    return f"   <{tag}><rdf:{kind}>\n{lines}    </rdf:{kind}></{tag}>\n"


def xmp_packet(data):
    """The XMP of `data` (see for_export()), as UTF-8 bytes."""
    body = ""
    if data.get("title"):
        body += _alt("dc:title", data["title"])
    if data.get("caption"):
        body += _alt("dc:description", data["caption"])
    if data.get("keywords"):
        body += _list("dc:subject", "Bag", data["keywords"])
    if data.get("creator"):
        body += _list("dc:creator", "Seq", [data["creator"]])
    if data.get("copyright"):
        body += _alt("dc:rights", data["copyright"])
        body += "   <xmpRights:Marked>True</xmpRights:Marked>\n"
    if data.get("contact"):
        kind = "CiEmailWork" if "@" in data["contact"] else "CiUrlWork"
        body += (
            '   <Iptc4xmpCore:CreatorContactInfo rdf:parseType="Resource">\n'
            f"    <Iptc4xmpCore:{kind}>{escape(data['contact'])}</Iptc4xmpCore:{kind}>\n"
            "   </Iptc4xmpCore:CreatorContactInfo>\n"
        )
    text = (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Tamis">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about=""\n'
        '    xmlns:dc="http://purl.org/dc/elements/1.1/"\n'
        '    xmlns:xmpRights="http://ns.adobe.com/xap/1.0/rights/"\n'
        '    xmlns:Iptc4xmpCore="http://iptc.org/std/Iptc4xmpCore/1.0/xmlns/">\n'
        f"{body}"
        "  </rdf:Description>\n"
        " </rdf:RDF>\n"
        "</x:xmpmeta>\n"
        '<?xpacket end="w"?>'
    )
    return text.encode("utf-8")


# -- IPTC-IIM ------------------------------------------------------------------

def _truncate(text, limit):
    """`text` in UTF-8, cut to `limit` bytes without splitting a character."""
    data = text.encode("utf-8")
    if len(data) <= limit:
        return data
    return data[:limit].decode("utf-8", "ignore").encode("utf-8")


def _dataset(record, number, value):
    return struct.pack(">BBBH", 0x1C, record, number, len(value)) + value


def iim_block(data):
    """The IPTC-IIM datasets of `data`, in UTF-8 (record 1:90 says so)."""
    out = _dataset(1, 90, b"\x1b%G")  # coded character set: UTF-8
    out += _dataset(2, 0, b"\x00\x04")  # record version 4
    for key in ("title", "creator", "copyright", "contact", "caption"):
        if data.get(key):
            number, limit = _IIM[key]
            out += _dataset(2, number, _truncate(data[key], limit))
    number, limit = _IIM["keywords"]
    for word in data.get("keywords", []):
        out += _dataset(2, number, _truncate(word, limit))
    return out


def photoshop_segment(data):
    """The APP13 payload: `Photoshop 3.0` and an 8BIM resource 0x0404 holding the IIM block."""
    block = iim_block(data)
    resource = b"8BIM" + struct.pack(">H", 0x0404) + b"\0\0" + struct.pack(">I", len(block)) + block
    if len(block) % 2:
        resource += b"\0"
    return PHOTOSHOP_HEADER + resource


# -- JPEG ----------------------------------------------------------------------

def _segment(marker, payload):
    return bytes((0xFF, marker)) + struct.pack(">H", len(payload) + 2) + payload


def with_metadata(jpeg, data):
    """`jpeg` (bytes) carrying `data` as XMP and IPTC; any XMP or IPTC it had is replaced.

    Unchanged when there is nothing to write. The segments go after the
    JFIF APP0 and EXIF APP1, where readers look for them.
    """
    if not data or jpeg[:2] != b"\xff\xd8":
        return jpeg
    xmp = XMP_HEADER + xmp_packet(data)
    photoshop = photoshop_segment(data)
    added = b""
    if len(xmp) <= _SEGMENT_LIMIT:
        added += _segment(0xE1, xmp)
    if len(photoshop) <= _SEGMENT_LIMIT:
        added += _segment(0xED, photoshop)
    out, i, placed = bytearray(b"\xff\xd8"), 2, False
    while i + 4 <= len(jpeg) and jpeg[i] == 0xFF and jpeg[i + 1] not in (0xD9, 0xDA):
        marker = jpeg[i + 1]
        (length,) = struct.unpack(">H", jpeg[i + 2:i + 4])
        part = jpeg[i:i + 2 + length]
        exif = marker == 0xE1 and part[4:10] == b"Exif\0\0"
        if not placed and marker != 0xE0 and not exif:
            out += added
            placed = True
        replaced = (marker == 0xE1 and part[4:4 + len(XMP_HEADER)] == XMP_HEADER) or (
            marker == 0xED and part[4:4 + len(PHOTOSHOP_HEADER)] == PHOTOSHOP_HEADER
        )
        if not replaced:
            out += part
        i += 2 + length
    if not placed:
        out += added
    return bytes(out + jpeg[i:])


def read_jpeg(jpeg):
    """(XMP text or None, IIM datasets as [(record, number, bytes)]) of a JPEG: for the tests and debugging."""
    xmp, datasets, i = None, [], 2
    while i + 4 <= len(jpeg) and jpeg[i] == 0xFF and jpeg[i + 1] not in (0xD9, 0xDA):
        marker = jpeg[i + 1]
        (length,) = struct.unpack(">H", jpeg[i + 2:i + 4])
        payload = jpeg[i + 4:i + 2 + length]
        if marker == 0xE1 and payload.startswith(XMP_HEADER):
            xmp = payload[len(XMP_HEADER):].decode("utf-8")
        elif marker == 0xED and payload.startswith(PHOTOSHOP_HEADER):
            j = len(PHOTOSHOP_HEADER)
            while j + 12 <= len(payload) and payload[j:j + 4] == b"8BIM":
                resource = struct.unpack(">H", payload[j + 4:j + 6])[0]
                name_length = payload[j + 6]
                j += 6 + name_length + 1 + ((name_length + 1) % 2)
                (size,) = struct.unpack(">I", payload[j:j + 4])
                block = payload[j + 4:j + 4 + size]
                j += 4 + size + size % 2
                k = 0
                while resource == 0x0404 and k + 5 <= len(block) and block[k] == 0x1C:
                    record, number, size2 = block[k + 1], block[k + 2], struct.unpack(">H", block[k + 3:k + 5])[0]
                    datasets.append((record, number, block[k + 5:k + 5 + size2]))
                    k += 5 + size2
        i += 2 + length
    return xmp, datasets
