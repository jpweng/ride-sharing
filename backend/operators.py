"""
Swiss public transport operators, for showing who actually runs each leg of
a connection (like flight search sites show airline logos).

transport.opendata.ch reports a journey's operator as a short code with
assorted suffixes: "SBB", "BLS-bls", "MGB-EV" (EV = Ersatzverkehr, a
replacement bus the same company runs), "TPF Auto" (its bus division),
"VBZ    F" (whitespace-padded). The part before the first dash/space is the
company; that's what this table is keyed on.

Logos are each operator's own favicon, fetched through Google's favicon
service rather than bundled into this repo -- no trademarked artwork to
redistribute, and it stays current if an operator rebrands. The frontend
falls back to a text badge if the image fails to load.

Unknown codes still come back with their code as the display name, just
without a link or logo, so a missing table entry degrades gracefully.
"""
import re
from urllib.parse import quote

# Base code (upper-cased) -> (display name, website domain).
OPERATORS = {
    "SBB": ("SBB CFF FFS", "sbb.ch"),
    "BLS": ("BLS", "bls.ch"),
    "SOB": ("Südostbahn", "sob.ch"),
    "RHB": ("Rhätische Bahn", "rhb.ch"),
    "MGB": ("Matterhorn Gotthard Bahn", "matterhorngotthardbahn.ch"),
    "ZB": ("Zentralbahn", "zentralbahn.ch"),
    "MOB": ("MOB GoldenPass", "mob.ch"),
    "TPF": ("TPF", "tpf.ch"),
    "TPC": ("Transports Publics du Chablais", "tpc.ch"),
    "TRAVYS": ("TRAVYS", "travys.ch"),
    "TRN": ("transN", "transn.ch"),
    "NSTCM": ("NStCM", "nstcm.ch"),
    "MBC": ("MBC", "mbc.ch"),
    "TMR": ("TMR", "tmrsa.ch"),
    "RA": ("RegionAlps", "regionalps.ch"),
    "THURBO": ("Thurbo", "thurbo.ch"),
    "AB": ("Appenzeller Bahnen", "appenzellerbahnen.ch"),
    "SZU": ("SZU", "szu.ch"),
    "ASM": ("Aare Seeland mobil", "aare-seeland-mobil.ch"),
    "RBS": ("RBS", "rbs.ch"),
    "BOB": ("Berner Oberland-Bahnen", "jungfrau.ch"),
    "JB": ("Jungfraubahnen", "jungfrau.ch"),
    "WAB": ("Wengernalpbahn", "jungfrau.ch"),
    "FART": ("FART", "fartiamo.ch"),
    "TILO": ("TILO", "tilo.ch"),
    "PAG": ("PostAuto", "postauto.ch"),
    # City / regional transport
    "VBZ": ("VBZ Zürich", "vbz.ch"),
    "SVB": ("BERNMOBIL", "bernmobil.ch"),
    "BVB": ("BVB Basel", "bvb.ch"),
    "BLT": ("BLT", "blt.ch"),
    "VBL": ("VBL Luzern", "vbl.ch"),
    "TL": ("tl Lausanne", "t-l.ch"),
    "TPG": ("TPG Genève", "tpg.ch"),
    "TPL": ("TPL Lugano", "tplsa.ch"),
    "VMCV": ("VMCV", "vmcv.ch"),
    "STI": ("STI", "sti.ch"),
    # Lake boats
    "SGV": ("SGV Lake Lucerne", "lakelucerne.ch"),
    "CGN": ("CGN", "cgn.ch"),
    # Cross-border
    "DB": ("Deutsche Bahn", "bahn.de"),
    "OBB": ("ÖBB", "oebb.at"),
    "ÖBB": ("ÖBB", "oebb.at"),
    "TGV": ("TGV Lyria", "tgv-lyria.com"),
    "TI": ("Trenitalia", "trenitalia.com"),
}


def base_code(raw: str | None) -> str | None:
    """'BLS-bls' -> 'BLS', 'TPF Auto' -> 'TPF', 'VBZ    F' -> 'VBZ'."""
    if not raw or not raw.strip():
        return None
    return re.split(r"[-\s]", raw.strip(), maxsplit=1)[0]


def operator_info(raw: str | None) -> dict | None:
    """
    {code, name, url, logo_url} for an operator code as the API reports it,
    or None if there's no operator (e.g. a walking section).
    """
    code = base_code(raw)
    if not code:
        return None
    known = OPERATORS.get(code.upper())
    if not known:
        return {"code": code, "name": code, "url": None, "logo_url": None}
    name, domain = known
    return {
        "code": code.upper(),
        "name": name,
        "url": f"https://www.{domain}",
        "logo_url": f"https://www.google.com/s2/favicons?domain={quote(domain)}&sz=64",
    }
