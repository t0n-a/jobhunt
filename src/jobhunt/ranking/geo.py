"""Country detection from a location string, and the visa class of a job.

EU/EEA is easy; outside it a job needs sponsorship or enough pay.
"""

import re

# EU citizens can live and work in CH, so it counts as EU/EEA for visa purposes.
EU_EEA = frozenset(
    {
        "AT",
        "BE",
        "BG",
        "HR",
        "CY",
        "CZ",
        "DK",
        "EE",
        "FI",
        "FR",
        "DE",
        "GR",
        "HU",
        "IE",
        "IT",
        "LV",
        "LT",
        "LU",
        "MT",
        "NL",
        "PL",
        "PT",
        "RO",
        "SK",
        "SI",
        "ES",
        "SE",
        "IS",
        "LI",
        "NO",
        "CH",
    }
)


# Lowercase names per country: English, local, capital and main tech cities, ISO-3 where common.
_BY_COUNTRY = {
    "PT": ["portugal", "portuguesa", "lisboa", "lisbon", "porto", "braga", "coimbra"],
    "ES": [
        "spain",
        "españa",
        "espana",
        "madrid",
        "barcelona",
        "valencia",
        "sevilla",
        "seville",
        "málaga",
        "malaga",
        "bilbao",
        "esp",
    ],
    "IT": [
        "italy",
        "italia",
        "milan",
        "milano",
        "rome",
        "roma",
        "turin",
        "torino",
        "florence",
        "firenze",
        "bologna",
        "ita",
    ],
    "FR": [
        "france",
        "paris",
        "lyon",
        "marseille",
        "toulouse",
        "bordeaux",
        "nantes",
        "lille",
        "montpellier",
        "fra",
    ],
    "DE": [
        "germany",
        "deutschland",
        "berlin",
        "munich",
        "münchen",
        "munchen",
        "hamburg",
        "frankfurt",
        "cologne",
        "köln",
        "koeln",
        "düsseldorf",
        "dusseldorf",
        "stuttgart",
        "dresden",
        "leipzig",
        "hannover",
        "hanover",
        "nuremberg",
        "nürnberg",
        "deu",
    ],
    "NL": [
        "netherlands",
        "nederland",
        "holland",
        "amsterdam",
        "rotterdam",
        "utrecht",
        "eindhoven",
        "the hague",
        "den haag",
        "nld",
    ],
    "BE": [
        "belgium",
        "belgië",
        "belgie",
        "belgique",
        "brussels",
        "bruxelles",
        "brussel",
        "antwerp",
        "antwerpen",
        "leuven",
        "liège",
        "liege",
    ],
    "AT": ["austria", "österreich", "osterreich", "vienna", "wien", "graz", "linz", "salzburg"],
    "IE": ["ireland", "éire", "eire", "dublin", "cork"],
    "LU": ["luxembourg", "luxemburg"],
    "DK": ["denmark", "danmark", "copenhagen", "københavn", "kobenhavn", "aarhus", "odense"],
    "SE": [
        "sweden",
        "sverige",
        "stockholm",
        "gothenburg",
        "göteborg",
        "goteborg",
        "malmö",
        "malmo",
        "uppsala",
    ],
    "FI": ["finland", "suomi", "helsinki", "helsingfors", "espoo", "tampere", "turku"],
    "NO": ["norway", "norge", "oslo", "bergen", "trondheim", "stavanger"],
    "IS": ["iceland", "ísland", "reykjavík", "reykjavik"],
    "CH": [
        "switzerland",
        "schweiz",
        "suisse",
        "svizzera",
        "zürich",
        "zurich",
        "geneva",
        "genève",
        "geneve",
        "basel",
        "bern",
        "berne",
        "lausanne",
        "lugano",
    ],
    "PL": [
        "poland",
        "polska",
        "warsaw",
        "warszawa",
        "krakow",
        "kraków",
        "wroclaw",
        "wrocław",
        "gdansk",
        "gdańsk",
    ],
    "CZ": ["czechia", "czech republic", "česko", "cesko", "prague", "praha", "brno"],
    "SK": ["slovakia", "slovensko", "bratislava"],
    "HU": ["hungary", "magyarország", "magyarorszag", "budapest"],
    "RO": ["romania", "românia", "bucharest", "bucuresti", "bucurești", "cluj", "cluj-napoca"],
    "BG": ["bulgaria", "българия", "sofia", "софия"],
    "GR": ["greece", "ellada", "ελλάδα", "athens", "αθήνα", "thessaloniki"],
    "HR": ["croatia", "hrvatska", "zagreb"],
    "SI": ["slovenia", "slovenija", "ljubljana"],
    "EE": ["estonia", "eesti", "tallinn", "tartu"],
    "LV": ["latvia", "latvija", "riga"],
    "LT": ["lithuania", "lietuva", "vilnius", "kaunas"],
    "CY": ["cyprus", "kypros", "nicosia", "limassol"],
    "MT": ["malta", "valletta"],
    "GB": [
        "united kingdom",
        "uk",
        "gbr",
        "great britain",
        "england",
        "scotland",
        "wales",
        "northern ireland",
        "london",
        "manchester",
        "edinburgh",
        "glasgow",
        "birmingham",
        "bristol",
        "leeds",
        "liverpool",
        "oxford",
        "belfast",
        "cardiff",
    ],
    "US": [
        "united states",
        "united states of america",
        "usa",
        "u.s.",
        "new york",
        "nyc",
        "san francisco",
        "san jose",
        "san diego",
        "los angeles",
        "palo alto",
        "mountain view",
        "menlo park",
        "sunnyvale",
        "redmond",
        "seattle",
        "boston",
        "chicago",
        "austin",
        "denver",
        "atlanta",
        "miami",
        "dallas",
        "houston",
        "portland",
        "phoenix",
        "philadelphia",
        "pittsburgh",
        "raleigh",
        "charlotte",
        "washington dc",
        "silicon valley",
        "bay area",
        "brooklyn",
    ],
    "CA": [
        "canada",
        "toronto",
        "vancouver",
        "montreal",
        "montréal",
        "ottawa",
        "calgary",
        "edmonton",
    ],
    "AU": ["australia", "sydney", "melbourne", "brisbane", "canberra", "adelaide"],
    "NZ": ["new zealand", "auckland", "wellington", "christchurch"],
    "SG": ["singapore", "sgp"],
    "JP": ["japan", "nihon", "nippon", "tokyo", "osaka", "yokohama", "jpn"],
    "AE": ["united arab emirates", "uae", "dubai", "abu dhabi"],
    "IL": ["israel", "tel aviv", "tel-aviv", "jerusalem", "haifa", "herzliya", "isr"],
    "BR": [
        "brazil",
        "brasil",
        "brasília",
        "brasilia",
        "são paulo",
        "sao paulo",
        "rio de janeiro",
        "belo horizonte",
        "florianopolis",
        "florianópolis",
        "bra",
    ],
    "IN": [
        "india",
        "bangalore",
        "bengaluru",
        "mumbai",
        "bombay",
        "delhi",
        "new delhi",
        "hyderabad",
        "pune",
        "chennai",
        "gurgaon",
        "gurugram",
        "noida",
        "kolkata",
    ],
}
_ALIASES = {name: code for code, names in _BY_COUNTRY.items() for name in names}
# Longest first, so "northern ireland" beats "ireland"; lookarounds, not \b, so "u.s." works.
_ALIAS_RE = re.compile(
    "(?<!\\w)("
    + "|".join(re.escape(a) for a in sorted(_ALIASES, key=len, reverse=True))
    + ")(?!\\w)"
)
_US_STATES = frozenset(
    {
        "AL",
        "AK",
        "AZ",
        "AR",
        "CA",
        "CO",
        "CT",
        "DE",
        "FL",
        "GA",
        "HI",
        "ID",
        "IL",
        "IN",
        "IA",
        "KS",
        "KY",
        "LA",
        "ME",
        "MD",
        "MA",
        "MI",
        "MN",
        "MS",
        "MO",
        "MT",
        "NE",
        "NV",
        "NH",
        "NJ",
        "NM",
        "NY",
        "NC",
        "ND",
        "OH",
        "OK",
        "OR",
        "PA",
        "RI",
        "SC",
        "SD",
        "TN",
        "TX",
        "UT",
        "VT",
        "VA",
        "WA",
        "WV",
        "WI",
        "WY",
        "DC",
    }
)

_TRAILING_CODE = re.compile(r",\s*([A-Z]{2})\s*$")

# Negation must sit in the same sentence, within 40 characters before the hit.
_SPONSOR_HIT = re.compile(
    r"visa sponsorship|\bsponsor (?:a|your) visa\b|\bwe sponsor\b"
    r"|relocation (?:package|support|assistance)|helps? you relocate",
    re.IGNORECASE,
)
_NEGATION = re.compile(r"\b(?:no|not|unable to|cannot|can't|don't|do not|won't)\b", re.IGNORECASE)


def country_of(location: str | None) -> str | None:
    if not location:
        return None
    text = location.strip()
    # A US state code is checked before aliases: "Paris, TX" is not France.
    state = _TRAILING_CODE.search(text)
    if state and state.group(1) in _US_STATES:
        return "US"
    m = _ALIAS_RE.search(text.lower())
    return _ALIASES[m.group(1)] if m else None


def mentions_sponsorship(text: str) -> bool:
    for m in _SPONSOR_HIT.finditer(text):
        window = text[max(0, m.start() - 40) : m.start()]
        if not _NEGATION.search(re.split(r"[.!?;\n]", window)[-1]):
            return True
    return False


def visa_class(
    country: str | None, description: str, pay_yearly_eur: int | None, min_pay_outside_eu: int
) -> str:
    if country is None or country in EU_EEA:
        return "easy"
    if mentions_sponsorship(description) or (
        pay_yearly_eur is not None and pay_yearly_eur >= min_pay_outside_eu
    ):
        return "ok"
    return "hard"
