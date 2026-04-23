"""EVE Online static reference data."""

ESI_BASE = "https://esi.evetech.net/latest"
ESI_HEADERS = {
    "User-Agent": "EVE-Market-Dashboard/2.0 (contact: github.com/eve-market-dash)",
    "Accept": "application/json",
}

# Trade hub solar system IDs
HUB_SYSTEMS: dict[str, int] = {
    "Jita":    30000142,
    "Amarr":   30002187,
    "Rens":    30002510,
    "Hek":     30002053,
    "Dodixie": 30002659,
}

# Region IDs (market data lives at region level)
HUB_TO_REGION: dict[str, int] = {
    "Jita":    10000002,  # The Forge
    "Amarr":   10000043,  # Domain
    "Rens":    10000030,  # Heimatar
    "Hek":     10000042,  # Metropolis
    "Dodixie": 10000032,  # Sinq Laison
}

REGION_NAMES: dict[int, str] = {v: k for k, v in HUB_TO_REGION.items()}

# Fee defaults — with good trading skills (Accounting 5, Broker Relations 5)
DEFAULT_BROKER_FEE = 0.025   # 2.5 % (base 3 % − 0.1 %/level × 5)
DEFAULT_SALES_TAX  = 0.036   # 3.6 % (base 8 % × (1 − 0.11 × 5))
DEFAULT_HAUL_PER_JUMP = 1_500_000  # ISK/jump flat hauling cost

# System security reference (extended list; ESI lookup fills the rest)
KNOWN_SECURITY: dict[int, tuple[str, float]] = {
    30000142: ("Jita",         0.9),
    30002187: ("Amarr",        1.0),
    30002510: ("Rens",         0.9),
    30002053: ("Hek",          0.8),
    30002659: ("Dodixie",      0.9),
    30000144: ("Perimeter",    1.0),
    30002645: ("Oursulaert",   0.9),
    30001647: ("Niarja",       0.5),   # notorious gank system
    30001651: ("Madirmilire",  0.4),
    30001654: ("Mista",        0.3),
    30001656: ("Osmeden",      0.6),
    30002757: ("Aurohunen",    0.7),
    30002681: ("Tintoh",       0.8),
    30002678: ("Odan",         0.8),
}

# Item group IDs to ignore (noisy / low-value)
BLACKLISTED_GROUPS: set[int] = {
    18,   # Minerals
    9,    # Drones
    330,  # Blueprints
    700,  # Abyssal modules
    754,  # Sovereignty Structures
    920,  # Sovereignty Bills
    4,    # Ammunition & Charges (optional — high volume but tiny margins)
}

# Colour palette used across the dashboard
COLOUR = {
    "bg":       "#050A0E",
    "card":     "#0F1923",
    "border":   "#1E3A5A",
    "accent":   "#00B4D8",
    "gold":     "#FFD700",
    "green":    "#39FF14",
    "red":      "#FF4444",
    "orange":   "#FFB347",
    "text":     "#C5C6C7",
    "subtext":  "#7A8A9A",
}
