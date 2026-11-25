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

There are two descriptions. Google Play's says, as the site does, that this is a demo for now.
The App Store's says nothing about the demo, like the iOS app itself, which shows the site
without it (docs/ios-app-without-demo.md): the note on top of the About page and its section
about the demo are left out.
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
LIMITS = {"name": 30, "subtitle": 30, "short": 80, "keywords": 100, "play": 4000, "app_store": 4000}

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
    "en": ("Name", "Subtitle (App Store)", "Short description (Google Play)", "Keywords (App Store)",
           "Description (Google Play)", "Description (App Store)"),
    "de": ("Name", "Untertitel (App Store)", "Kurzbeschreibung (Google Play)", "Keywords (App Store)",
           "Beschreibung (Google Play)", "Beschreibung (App Store)"),
    "fr": ("Nom", "Sous-titre (App Store)", "Description courte (Google Play)", "Mots-clés (App Store)",
           "Description (Google Play)", "Description (App Store)"),
    "it": ("Nome", "Sottotitolo (App Store)", "Descrizione breve (Google Play)", "Parole chiave (App Store)",
           "Descrizione (Google Play)", "Descrizione (App Store)"),
}

# The About page's sections (its <h3>), in order, and which listing has them.
EVERYWHERE, NOWHERE, WITH_THE_DEMO = "everywhere", "nowhere", "only where the demo is mentioned"
SECTIONS = (
    ("a passenger's side", EVERYWHERE),
    ("a driver's side", EVERYWHERE),
    ("sharing costs", EVERYWHERE),
    ("who you travel with", EVERYWHERE),
    ("phones and computers", NOWHERE),  # "apps are coming soon"
    ("the demo", WITH_THE_DEMO),
    ("your data", EVERYWHERE),
)
# The class of the note about the demo on top of the page.
DEMO_NOTE = "demo-hint"
BLOCKS = {"h2", "h3", "p", "li"}


class _About(HTMLParser):
    """The page's articles as {language: [(tag, text)]}: its headings, paragraphs and list
    items in order, without anything marked data-payments. The note about the demo on top of
    the page has the tag "demo-note"."""

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
            self.block = (tag, [], DEMO_NOTE in (attrs.get("class") or "").split())

    def handle_endtag(self, tag):
        if tag == "article":
            self.language = None
        elif self.skipping:
            self.skipping.pop()
        elif self.block and tag == self.block[0]:
            text = " ".join("".join(self.block[1]).split())
            if text:
                self.articles[self.language].append(("demo-note" if self.block[2] else tag, text))
            self.block = None

    def handle_data(self, data):
        if self.block and not self.skipping:
            self.block[1].append(data)


def description(blocks: list[tuple[str, str]], with_the_demo: bool = True) -> str:
    """A language's About page as a store description: plain text, since neither store shows
    any formatting. `with_the_demo`: with what the page says about the demo (Google Play), or
    without it (the App Store)."""
    wanted_kinds = {EVERYWHERE, WITH_THE_DEMO} if with_the_demo else {EVERYWHERE}
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
    lines = "\n\n".join(text for tag, text in opening if with_the_demo or tag != "demo-note").split("\n")
    for (_, kind), section in zip(SECTIONS, sections):
        if kind not in wanted_kinds:
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
        blocks = parser.articles[language]
        fields = (NAME, subtitle, SHORT[language], KEYWORDS[language],
                  description(blocks), description(blocks, with_the_demo=False))
        parts = ["<!-- Written by mobile/make_listing.py from frontend/about.html: change those, not this file. -->\n"]
        for heading, value in zip(HEADINGS[language], fields):
            # In a code block: what a store's field gets is exactly what is between the fences.
            parts.append(f"## {heading}\n\n```text\n{value.rstrip()}\n```\n")
        out[language] = "\n".join(parts)
    return out


def fields(listing: str) -> dict[str, str]:
    """A listing file's texts, by the keys of LIMITS."""
    values = [chunk.split("\n```", 1)[0] for chunk in listing.split("```text\n")[1:]]
    return dict(zip(("name", "subtitle", "short", "keywords", "play", "app_store"), values))


def main():
    OUT.mkdir(exist_ok=True)
    for language, text in listings().items():
        (OUT / f"{language}.md").write_text(text)
        sizes = ", ".join(f"{key} {len(value)}/{LIMITS[key]}" for key, value in fields(text).items())
        print(f"wrote mobile/listing/{language}.md ({sizes})")


if __name__ == "__main__":
    main()
