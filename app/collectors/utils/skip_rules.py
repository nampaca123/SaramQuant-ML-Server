SKIP_INDICES = frozenset({
    "1001", "1002", "1003", "1004", "1027", "1028", "1034", "1035",
    "1150", "1151", "1152", "1153", "1154", "1155", "1156", "1157",
    "1158", "1159", "1160", "1167", "1168", "1182", "1224", "1227",
    "1232", "1244", "1894",
    "2001", "2002", "2003", "2004", "2024",
    "2181", "2182", "2183", "2184", "2189",
    "2203", "2212", "2213", "2214", "2215", "2216", "2217", "2218",
})

_KR_SKIP_NAMES = ("스팩",)


def is_skippable_kr_name(name: str) -> bool:
    return any(keyword in name for keyword in _KR_SKIP_NAMES)


def is_valid_us_symbol(symbol: str) -> bool:
    return bool(symbol) and len(symbol) <= 5 and symbol.isalpha()
