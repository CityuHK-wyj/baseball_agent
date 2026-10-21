"""Isolated worker for validated content-resolution programs; no application imports."""

import json
import re
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser


class _Nodes(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attributes": dict(attrs), "text": "", "children": []}
        if self.stack:
            self.stack[-1]["children"].append(node)
        self.nodes.append(node)
        self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        for node in self.stack:
            node["text"] += data


def html_nodes(source):
    parser = _Nodes()
    parser.feed(source[:160_000])
    return parser.nodes[:3000]


def xml_nodes(source):
    root = ET.fromstring(source[:160_000])
    return [{"tag": node.tag, "attributes": dict(node.attrib), "text": node.text or ""} for node in list(root.iter())[:3000]]


def find_all(source, pattern):
    if len(pattern) > 200 or len(source) > 160_000:
        return []
    return re.findall(pattern, source)[:100]


def get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    if isinstance(obj, list) and isinstance(key, int) and -len(obj) <= key < len(obj):
        return obj[key]
    return default


def main():
    payload = json.loads(sys.stdin.read(400_000))
    safe = {
        "len": len, "str": str, "int": int, "float": float, "bool": bool,
        "range": range, "enumerate": enumerate, "min": min, "max": max,
        "sum": sum, "sorted": sorted, "any": any, "all": all,
        "get": get, "json_parse": json.loads, "html_nodes": html_nodes,
        "xml_nodes": xml_nodes, "find_all": find_all,
        "join": lambda items, separator="": separator.join(items),
        "lower": lambda value: value.lower(), "strip": lambda value: value.strip(),
    }
    scope = {"__builtins__": safe, "text": payload["text"], "data": payload.get("data")}
    exec(compile(payload["program"], "<resolution>", "exec"), scope, scope)
    result = scope.get("result")
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode()) > 32_000:
        raise ValueError("OUTPUT_LIMIT")
    sys.stdout.write(encoded)


if __name__ == "__main__":
    main()
