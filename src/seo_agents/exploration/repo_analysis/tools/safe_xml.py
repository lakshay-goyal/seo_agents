"""Parse untrusted repository XML/SVG without entity expansion or external fetches.

Uses `defusedxml` when installed. Otherwise falls back to the stdlib parser after
rejecting ENTITY declarations (billion-laughs / external entities); the stdlib
parser never fetches external DTDs. Both paths raise `XMLParseError` (a ValueError) on bad input.
"""

import re
import xml.etree.ElementTree as _ET

try:  # pragma: no cover - depends on the environment
    from defusedxml import ElementTree as _DEFUSED
    from defusedxml import DefusedXmlException as _DefusedError
except ImportError:  # pragma: no cover
    _DEFUSED, _DefusedError = None, None

ParseError = _ET.ParseError
_DANGEROUS = re.compile(rb'<!\s*ENTITY', re.I)


class XMLParseError(ValueError):
    """Malformed or unsafe XML."""


def safe_xml_fromstring(data):
    raw = data.encode() if isinstance(data, str) else bytes(data)
    if _DEFUSED is not None:
        try:
            return _DEFUSED.fromstring(raw)
        except (_ET.ParseError, _DefusedError, ValueError) as error:
            raise XMLParseError(str(error)) from error
    if _DANGEROUS.search(raw):
        raise XMLParseError('XML ENTITY declarations are not parsed (defusedxml unavailable)')
    try:
        return _ET.fromstring(raw)
    except _ET.ParseError as error:
        raise XMLParseError(str(error)) from error
