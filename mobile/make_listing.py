"""
Writes the store listings, mobile/listing/<lang>.md, for Google Play and the App Store.

A listing describes the service, and there is one description of the service: the About page
(frontend/about.html, see docs/about-page.md). So the listing's long description is made of the
About page's own sentences, in its four languages, and is written again whenever the page changes:

    uv run python mobile/make_listing.py

backend/tests/test_store_listing.py fails when the committed listings are not what this script
writes, or when a text is longer than a store allows.

Left out of the description: what depends on payments being switched on (the page shows one text
or the other; the listing says neither), and the section about phones and computers (it says the
apps are coming). The few texts a store wants that the page doesn't have (short description,
keywords) are below.
"""
import json
from html.parser import HTMLParser
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ABOUT = REPO / "frontend/about.html"
OUT = Path(__file__).resolve().parent / "listing"
LANGUAGES = ("en", "de", "fr", "it")

NAME = "Ride and Rail"
# Both stores: characters at most.
LIMITS = {"name": 30, "subtitle": 30, "short": 80, "keywords": 100, "description": 4000}

# Google Play's short description. The tagline, then public transport as what completes a
# ride, in the order docs/about-page.md asks for.
SHORT = {
    "en": "Carpooling for Switzerland. A ride can be combined with public transport.",
    "de": "Mitfahren für die Schweiz. Eine Mitfahrt lässt sich mit dem ÖV kombinieren.",
    "fr": "Le covoiturage pour la Suisse, à combiner avec les transports publics.",
    "it": "Il car pooling per la Svizzera, da combinare con i trasporti pubblici.",
}
# The App Store's keywords: what people type, without the app's name or anybody's brand.
KEYWORDS = {
    "en": "carpool,carpooling,ride sharing,lift,commute,festival,train,public transport,Switzerland",
    "de": "Mitfahrgelegenheit,Fahrgemeinschaft,Mitfahren,Pendeln,Festival,Zug,ÖV,Schweiz,Carpooling",
    "fr": "covoiturage,trajet,pendulaire,festival,train,transports publics,Suisse,voiture,partage",
    "it": "car pooling,passaggio,pendolari,festival,treno,trasporti pubblici,Svizzera,auto,viaggio",
}
HEADINGS = {
    "en": ("Name", "Subtitle (App Store)", "Short description (Google Play)", "Keywords (App Store)", "Description"),
    "de": ("Name", "Untertitel (App Store)", "Kurzbeschreibung (Google Play)", "Keywords (App Store)", "Beschreibung"),
    "fr": ("Nom", "Sous-titre (App Store)", "Description courte (Google Play)", "Mots-clés (App Store)", "Description"),
    "it": ("Nome", "Sottotitolo (App Store)", "Descrizione breve (Google Play)", "Parole chiave (App Store)", "Descrizione"),
}

# The About page's sections (its <h3>), in order, and whether the listing has them.
SECTIONS = (
    ("a passenger's side", True),
    ("a driver's side", True),
    ("sharing costs", True),
    ("who you travel with", True),
    ("phones and computers", False),  # "apps are coming soon"
    ("the demo", True),
    ("your data", True),
)
BLOCKS = {"h2", "h3", "p", "li"}


class _About(HTMLParser):
    """The page's articles as {language: [(tag, text)]}: its headings, paragraphs and list
    items in order, without anything marked data-payments."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.articles = {}
        self.language = None
        self.block = None      # (tag, [text parts])
        self.skipping = []     # the open tags inside something marked data-payments

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "article":
            self.language = attrs.get("lang")
            self.articles[self.language] = []
        elif self.language is None:
            return
        elif self.skipping or "data-payments" in attrs:
            self.skipping.append(tag)
        elif tag in BLOCKS:
            self.block = (tag, [])

    def handle_endtag(self, tag):
        if tag == "article":
            self.language = None
        elif self.skipping:
            self.skipping.pop()
        elif self.block and tag == self.block[0]:
            text = " ".join("".join(self.block[1]).split())
            if text:
                self.articles[self.language].append((tag, text))
            self.block = None

    def handle_data(self, data):
        if self.block and not self.skipping:
            self.block[1].append(data)


def description(blocks: list[tuple[str, str]]) -> str:
    """A language's About page as a store description: plain text, since neither store shows
    any formatting."""
    sections, current = [], []  # current: what comes before the first <h3>
    for tag, text in blocks:
        if tag == "h2":
            continue  # the page's title
        if tag == "h3":
            sections.append(current)
            current = [(tag, text)]
        else:
            current.append((tag, text))
    sections.append(current)
    opening, sections = sections[0], sections[1:]
    if len(sections) != len(SECTIONS):
        raise SystemExit(
            f"frontend/about.html has {len(sections)} sections, mobile/make_listing.py knows {len(SECTIONS)}: "
            "say in SECTIONS which of them belong in the store listing"
        )
    lines = "\n\n".join(text for _, text in opening).split("\n")
    for (_, wanted), section in zip(SECTIONS, sections):
        if not wanted:
            continue
        if section is sections[-1]:
            section = section[:2]  # the heading and what it says; not the links that close the page
        for tag, text in section:
            if tag == "h3":
                lines += ["", text.upper()]
            else:
                lines.append(f"• {text}" if tag == "li" else text)
    return "\n".join(lines) + "\n"


def listings() -> dict[str, str]:
    """{language: the content of mobile/listing/<language>.md}"""
    parser = _About()
    parser.feed(ABOUT.read_text())
    out = {}
    for language in LANGUAGES:
        subtitle = json.loads((REPO / f"frontend/i18n/{language}/header.json").read_text())["header.subtitle"]
        fields = (NAME, subtitle, SHORT[language], KEYWORDS[language], description(parser.articles[language]))
        parts = ["<!-- Written by mobile/make_listing.py from frontend/about.html: change those, not this file. -->\n"]
        for heading, value in zip(HEADINGS[language], fields):
            # In a code block: what a store's field gets is exactly what is between the fences.
            parts.append(f"## {heading}\n\n```text\n{value.rstrip()}\n```\n")
        out[language] = "\n".join(parts)
    return out


def fields(listing: str) -> dict[str, str]:
    """A listing file's texts, by the keys of LIMITS."""
    values = [chunk.split("\n```", 1)[0] for chunk in listing.split("```text\n")[1:]]
    return dict(zip(("name", "subtitle", "short", "keywords", "description"), values))


def main():
    OUT.mkdir(exist_ok=True)
    for language, text in listings().items():
        (OUT / f"{language}.md").write_text(text)
        sizes = ", ".join(f"{key} {len(value)}/{LIMITS[key]}" for key, value in fields(text).items())
        print(f"wrote mobile/listing/{language}.md ({sizes})")


if __name__ == "__main__":
    main()
