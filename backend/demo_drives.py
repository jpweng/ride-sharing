"""
Keeps a rolling week of fictional drives in the database, so "Browse rides"
and matching always have something to show on the public demo.

The drives form a network over the whole country, denser in Ticino and
Graubünden: weekday commutes (out in the morning, back in the evening),
midday drives on the busy corridors, and weekend trips. Each corridor has
departures spread over the day, so a search at most times finds something
within matching.MAX_TIME_WINDOW_HOURS. A hand-written core of drives is
filled out by generated ones: about 3,000 commuters from the cities and
about 100 smaller towns, and departures every hour or two on 41 corridors:
about 36,000 drives a week in all. Each trip's road route is stored once, in
the demo_routes table, not on each of its drives.

Demo drives are flagged `is_demo` and are the only drives touched here:
drives posted by visitors are never modified or deleted. refresh() is
idempotent; maintenance.py runs it at startup and then hourly. It drops demo
drives that have departed or no longer match DEMO_DRIVES, and adds the ones
missing from the next DAYS_AHEAD days.

Run it by hand with the backend on PYTHONPATH, e.g.:
    PYTHONPATH=backend uv run python backend/demo_drives.py
"""
import random
import time as clock
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import database as db
import matching

DAYS_AHEAD = 7

# Departure times are naive Swiss local time, like the ones the frontend's
# datetime-local input sends.
LOCAL_TZ = ZoneInfo("Europe/Zurich")

# The public OSRM server asks for at most one request per second.
ROUTING_PAUSE_S = 1.0

MON_FRI = (0, 1, 2, 3, 4)
DAILY = (0, 1, 2, 3, 4, 5, 6)
SAT, SUN = (5,), (6,)

# Town centre or main station, (lat, lon).
PLACES = {
    # Romandie
    "Genève": (46.2044, 6.1432),
    "Nyon": (46.3833, 6.2333),
    "Lausanne": (46.5197, 6.6323),
    "Yverdon-les-Bains": (46.7785, 6.6411),
    "Vevey": (46.4628, 6.8419),
    "Montreux": (46.4312, 6.9107),
    "Bulle": (46.6193, 7.0569),
    "Fribourg": (46.8065, 7.1619),
    "Martigny": (46.1027, 7.0726),
    "Sion": (46.2331, 7.3606),
    "Sierre": (46.2919, 7.5354),
    "Brig": (46.3159, 7.9876),
    "Neuchâtel": (46.9900, 6.9293),
    "La Chaux-de-Fonds": (47.1035, 6.8328),
    "Delémont": (47.3649, 7.3445),
    "Biel/Bienne": (47.1368, 7.2467),
    # German-speaking Switzerland
    "Bern": (46.9480, 7.4474),
    "Thun": (46.7580, 7.6280),
    "Interlaken": (46.6863, 7.8632),
    "Solothurn": (47.2088, 7.5323),
    "Olten": (47.3500, 7.9033),
    "Aarau": (47.3925, 8.0444),
    "Basel": (47.5596, 7.5886),
    "Luzern": (47.0502, 8.3093),
    "Zug": (47.1662, 8.5155),
    "Zürich": (47.3769, 8.5417),
    "Winterthur": (47.4988, 8.7237),
    "Schaffhausen": (47.6973, 8.6349),
    "St. Gallen": (47.4245, 9.3767),
    "Sargans": (47.0450, 9.4400),
    # Ticino
    "Airolo": (46.5286, 8.6106),
    "Biasca": (46.3597, 8.9696),
    "Bellinzona": (46.1946, 9.0175),
    "Locarno": (46.1709, 8.7995),
    "Cevio": (46.3175, 8.6020),
    "Lugano": (46.0037, 8.9511),
    "Ponte Tresa": (45.9693, 8.8607),
    "Mendrisio": (45.8703, 8.9817),
    "Chiasso": (45.8320, 9.0312),
    "Roveredo": (46.2367, 9.1270),
    # Graubünden
    "Chur": (46.8508, 9.5320),
    "Landquart": (46.9667, 9.5550),
    "Klosters": (46.8696, 9.8813),
    "Davos": (46.8027, 9.8360),
    "Arosa": (46.7833, 9.6790),
    "Flims": (46.8350, 9.2840),
    "Ilanz": (46.7740, 9.2040),
    "Disentis/Mustér": (46.7036, 8.8530),
    "Thusis": (46.6970, 9.4400),
    "St. Moritz": (46.4908, 9.8355),
    "Zernez": (46.6990, 10.0920),
    "Scuol": (46.7966, 10.2980),
    "Poschiavo": (46.3240, 10.0580),
    # Smaller towns, for the generated commuters below.
    # Romandie
    "Morges": (46.5113, 6.4985),
    "Rolle": (46.4590, 6.3370),
    "Gland": (46.4207, 6.2700),
    "Renens": (46.5397, 6.5880),
    "Échallens": (46.6420, 6.6330),
    "Moudon": (46.6680, 6.7975),
    "Payerne": (46.8219, 6.9381),
    "Estavayer-le-Lac": (46.8490, 6.8460),
    "Romont": (46.6966, 6.9186),
    "Châtel-Saint-Denis": (46.5270, 6.9010),
    "Aigle": (46.3180, 6.9700),
    "Monthey": (46.2550, 6.9540),
    "Bex": (46.2500, 7.0110),
    "Verbier": (46.0960, 7.2280),
    "Crans-Montana": (46.3110, 7.4800),
    "Sainte-Croix": (46.8220, 6.5030),
    "Le Locle": (47.0560, 6.7480),
    "Saint-Imier": (47.1530, 6.9970),
    "Moutier": (47.2790, 7.3700),
    "Porrentruy": (47.4150, 7.0750),
    # German-speaking Switzerland
    "Visp": (46.2940, 7.8810),
    "Murten": (46.9280, 7.1170),
    "Lyss": (47.0740, 7.3060),
    "Grenchen": (47.1920, 7.3960),
    "Burgdorf": (47.0590, 7.6280),
    "Langnau im Emmental": (46.9400, 7.7870),
    "Langenthal": (47.2150, 7.7960),
    "Zofingen": (47.2880, 7.9460),
    "Spiez": (46.6880, 7.6800),
    "Frutigen": (46.5870, 7.6490),
    "Meiringen": (46.7270, 8.1870),
    "Grindelwald": (46.6240, 8.0410),
    "Zweisimmen": (46.5550, 7.3730),
    "Gstaad": (46.4750, 7.2860),
    "Liestal": (47.4840, 7.7340),
    "Rheinfelden": (47.5540, 7.7940),
    "Laufen": (47.4210, 7.5000),
    "Baden": (47.4730, 8.3080),
    "Brugg": (47.4860, 8.2080),
    "Lenzburg": (47.3880, 8.1800),
    "Wohlen": (47.3510, 8.2780),
    "Dietikon": (47.4040, 8.4000),
    "Bülach": (47.5220, 8.5400),
    "Kloten": (47.4510, 8.5850),
    "Uster": (47.3480, 8.7180),
    "Wetzikon": (47.3260, 8.7980),
    "Rapperswil": (47.2260, 8.8180),
    "Horgen": (47.2600, 8.5980),
    "Meilen": (47.2700, 8.6450),
    "Pfäffikon SZ": (47.2010, 8.7780),
    "Einsiedeln": (47.1280, 8.7470),
    "Schwyz": (47.0200, 8.6530),
    "Altdorf": (46.8800, 8.6440),
    "Stans": (46.9580, 8.3660),
    "Sarnen": (46.8960, 8.2460),
    "Engelberg": (46.8200, 8.4030),
    "Sursee": (47.1710, 8.1110),
    "Frauenfeld": (47.5580, 8.8980),
    "Wil": (47.4610, 9.0450),
    "Weinfelden": (47.5670, 9.1070),
    "Kreuzlingen": (47.6500, 9.1750),
    "Romanshorn": (47.5650, 9.3790),
    "Arbon": (47.5160, 9.4330),
    "Rorschach": (47.4780, 9.4900),
    "Herisau": (47.3860, 9.2790),
    "Appenzell": (47.3310, 9.4090),
    "Wattwil": (47.2990, 9.0870),
    "Buchs SG": (47.1670, 9.4780),
    "Glarus": (47.0400, 9.0680),
    # Ticino and Italian-speaking Graubünden
    "Faido": (46.4780, 8.8010),
    "Olivone": (46.5300, 8.9420),
    "Acquarossa": (46.4550, 8.9390),
    "Giubiasco": (46.1730, 9.0070),
    "Gordola": (46.1800, 8.8660),
    "Ascona": (46.1570, 8.7720),
    "Brissago": (46.1190, 8.7100),
    "Maggia": (46.2490, 8.7070),
    "Rivera": (46.1260, 8.9220),
    "Tesserete": (46.0680, 8.9650),
    "Agno": (45.9990, 8.9010),
    "Caslano": (45.9730, 8.8800),
    "Melide": (45.9550, 8.9480),
    "Stabio": (45.8490, 8.9370),
    "Grono": (46.2480, 9.1490),
    "Mesocco": (46.3940, 9.2330),
    "Brusio": (46.2590, 10.1240),
    # Graubünden
    "Domat/Ems": (46.8340, 9.4500),
    "Bonaduz": (46.8100, 9.3980),
    "Trimmis": (46.9000, 9.5600),
    "Maienfeld": (47.0090, 9.5300),
    "Schiers": (46.9700, 9.6870),
    "Lenzerheide": (46.7280, 9.5570),
    "Tiefencastel": (46.6600, 9.5780),
    "Savognin": (46.5960, 9.5980),
    "Bergün": (46.6300, 9.7470),
    "Andeer": (46.6030, 9.4270),
    "Splügen": (46.5510, 9.3220),
    "Laax": (46.8070, 9.2570),
    "Trun": (46.7430, 8.9870),
    "Vals": (46.6170, 9.1800),
    "Samedan": (46.5340, 9.8720),
    "Pontresina": (46.4910, 9.9020),
    "Silvaplana": (46.4590, 9.7950),
    "Zuoz": (46.6020, 9.9600),
    "Müstair": (46.6230, 10.4470),
}

# Where generated drivers' names come from, by the place they start from;
# everywhere else is German-speaking.
FRENCH_SPEAKING = {
    "Genève", "Nyon", "Lausanne", "Yverdon-les-Bains", "Vevey", "Montreux", "Bulle",
    "Fribourg", "Martigny", "Sion", "Sierre", "Neuchâtel", "La Chaux-de-Fonds",
    "Delémont", "Biel/Bienne", "Morges", "Rolle", "Gland", "Renens", "Échallens",
    "Moudon", "Payerne", "Estavayer-le-Lac", "Romont", "Châtel-Saint-Denis", "Aigle",
    "Monthey", "Bex", "Verbier", "Crans-Montana", "Sainte-Croix", "Le Locle",
    "Saint-Imier", "Moutier", "Porrentruy",
}
ITALIAN_SPEAKING = {
    "Airolo", "Biasca", "Bellinzona", "Locarno", "Cevio", "Lugano", "Ponte Tresa",
    "Mendrisio", "Chiasso", "Roveredo", "Poschiavo", "Faido", "Olivone", "Acquarossa",
    "Giubiasco", "Gordola", "Ascona", "Brissago", "Maggia", "Rivera", "Tesserete",
    "Agno", "Caslano", "Melide", "Stabio", "Grono", "Mesocco", "Brusio",
}
# Romansh and German names mixed.
GRAUBUNDEN = {
    "Chur", "Landquart", "Klosters", "Davos", "Arosa", "Flims", "Ilanz",
    "Disentis/Mustér", "Thusis", "St. Moritz", "Zernez", "Scuol", "Domat/Ems",
    "Bonaduz", "Trimmis", "Maienfeld", "Schiers", "Lenzerheide", "Tiefencastel",
    "Savognin", "Bergün", "Andeer", "Splügen", "Laax", "Trun", "Vals", "Samedan",
    "Pontresina", "Silvaplana", "Zuoz", "Müstair",
}


def _drive(driver, origin, destination, at, weekdays, seats=3):
    hours, minutes = map(int, at.split(":"))
    return {
        "driver_name": driver,
        "origin": {"name": origin, "lat": PLACES[origin][0], "lon": PLACES[origin][1]},
        "destination": {"name": destination, "lat": PLACES[destination][0], "lon": PLACES[destination][1]},
        "time": time(hours, minutes),
        "weekdays": weekdays,
        "seats_available": seats,
    }


def _commute(driver, home, work, out, back, weekdays=MON_FRI, seats=3):
    """There in the morning, back in the evening, by the same driver."""
    return [
        _drive(driver, home, work, out, weekdays, seats),
        _drive(driver, work, home, back, weekdays, seats),
    ]


# Recurring drives: one drive per listed weekday (Monday = 0) at the given
# local time. A driver never has two drives at the same time on one day.
DEMO_DRIVES = [
    # --- Romandie ---------------------------------------------------------
    *_commute("Jean Dupont", "Lausanne", "Genève", "07:15", "17:45"),
    *_commute("Sarah Pittet", "Nyon", "Genève", "07:40", "18:10", seats=2),
    *_commute("Anne Jaquier", "Yverdon-les-Bains", "Lausanne", "07:30", "17:30"),
    *_commute("Claire Rochat", "Vevey", "Fribourg", "07:20", "17:15"),
    *_commute("Nicolas Favre", "Montreux", "Bern", "06:45", "17:30", seats=2),
    *_commute("Julien Genoud", "Bulle", "Lausanne", "07:00", "18:00"),
    *_commute("Marco Rossi", "Fribourg", "Lausanne", "07:05", "17:20", seats=4),
    *_commute("Laure Chappuis", "Sion", "Lausanne", "06:30", "17:15"),
    *_commute("Pierre-Alain Roduit", "Martigny", "Genève", "07:00", "18:00", weekdays=(0, 2, 4)),
    *_commute("Kevin Aymon", "Sierre", "Sion", "07:20", "17:40"),
    *_commute("Stefan Imboden", "Brig", "Sion", "06:50", "17:20", seats=2),
    *_commute("Thomas Perret", "Neuchâtel", "Bern", "07:10", "17:20"),
    *_commute("Mélanie Cuche", "La Chaux-de-Fonds", "Neuchâtel", "07:00", "17:00"),
    *_commute("Damien Juillerat", "Delémont", "Biel/Bienne", "07:15", "17:10"),
    # Midday and evening on the Lake Geneva - Fribourg - Bern corridor.
    _drive("Olivier Bovet", "Lausanne", "Bern", "10:00", DAILY),
    _drive("Olivier Bovet", "Bern", "Lausanne", "14:30", DAILY),
    _drive("Élodie Morel", "Vevey", "Fribourg", "12:30", (0, 2, 4, 5)),
    _drive("Élodie Morel", "Fribourg", "Vevey", "15:00", (1, 3, 6)),
    _drive("Lucas Monney", "Vevey", "Fribourg", "16:30", MON_FRI, seats=2),
    _drive("Lucas Monney", "Fribourg", "Vevey", "19:30", (4, 6), seats=2),
    _drive("Isabelle Mottier", "Genève", "Lausanne", "12:00", DAILY, seats=2),
    _drive("Isabelle Mottier", "Lausanne", "Genève", "20:00", (4, 6), seats=2),
    # Weekends.
    _drive("Luca Bianchi", "Neuchâtel", "Genève", "09:30", SAT),
    _drive("Luca Bianchi", "Genève", "Neuchâtel", "18:00", SUN),
    _drive("Camille Dubois", "Genève", "Sion", "08:30", SAT),
    _drive("Camille Dubois", "Sion", "Genève", "17:00", SUN),
    # --- German-speaking Switzerland ---------------------------------------
    *_commute("Sophie Meier", "Zürich", "Bern", "07:45", "17:15", seats=2),
    *_commute("Anna Keller", "Basel", "Zürich", "08:00", "17:45", weekdays=(0, 2, 4), seats=1),
    *_commute("Reto Brunner", "Luzern", "Zürich", "07:00", "17:30"),
    *_commute("Andrea Steiner", "Thun", "Bern", "07:30", "17:00"),
    *_commute("Lukas Huber", "Winterthur", "Zürich", "07:15", "17:40"),
    *_commute("Nadine Frei", "St. Gallen", "Zürich", "06:50", "17:20"),
    *_commute("Marco Bühler", "Schaffhausen", "Zürich", "07:10", "17:50", seats=2),
    *_commute("Daniela Graf", "Aarau", "Basel", "07:30", "17:10"),
    *_commute("Simon Wyss", "Solothurn", "Bern", "07:20", "17:30"),
    *_commute("Martina Kälin", "Zug", "Zürich", "07:40", "18:00"),
    *_commute("Beat Schmid", "Olten", "Luzern", "07:15", "17:15"),
    *_commute("Urs Gerber", "Basel", "Bern", "08:30", "16:45"),
    _drive("Fabienne Roth", "Bern", "Zürich", "11:30", DAILY),
    _drive("Fabienne Roth", "Zürich", "Bern", "15:00", DAILY),
    _drive("Peter Zbinden", "Bern", "Interlaken", "09:00", SAT),
    _drive("Peter Zbinden", "Interlaken", "Bern", "17:30", SUN),
    _drive("Jonas Baumann", "Zürich", "Luzern", "10:00", SAT),
    _drive("Jonas Baumann", "Luzern", "Zürich", "18:30", SUN),
    # --- Ticino ------------------------------------------------------------
    *_commute("Giulia Bernasconi", "Lugano", "Bellinzona", "07:15", "17:30"),
    *_commute("Matteo Pedrazzini", "Locarno", "Bellinzona", "07:00", "17:15"),
    *_commute("Chiara Lepori", "Mendrisio", "Lugano", "07:30", "17:45"),
    *_commute("Davide Cattaneo", "Chiasso", "Lugano", "07:10", "18:00", seats=2),
    *_commute("Sara Guscetti", "Locarno", "Lugano", "07:45", "17:30"),
    *_commute("Fabio Rusconi", "Biasca", "Lugano", "06:50", "17:10", seats=4),
    *_commute("Elena Fumagalli", "Airolo", "Bellinzona", "07:00", "17:20"),
    *_commute("Nicola Respini", "Cevio", "Locarno", "07:10", "17:40"),
    *_commute("Alessia Ferrari", "Ponte Tresa", "Lugano", "07:30", "17:30", seats=2),
    *_commute("Lisa Togni", "Roveredo", "Bellinzona", "07:20", "17:15"),
    _drive("Giorgio Brenni", "Bellinzona", "Lugano", "10:00", DAILY),
    _drive("Giorgio Brenni", "Lugano", "Bellinzona", "13:30", DAILY),
    _drive("Paola Rezzonico", "Lugano", "Locarno", "12:00", DAILY, seats=2),
    _drive("Paola Rezzonico", "Locarno", "Lugano", "14:30", DAILY, seats=2),
    _drive("Tiziano Gendotti", "Bellinzona", "Airolo", "11:00", DAILY),
    _drive("Tiziano Gendotti", "Airolo", "Bellinzona", "15:30", DAILY),
    _drive("Ivo Croci", "Lugano", "Mendrisio", "20:00", (4, 5)),
    _drive("Ivo Croci", "Mendrisio", "Lugano", "19:00", MON_FRI),
    # Across the Alps.
    _drive("Marco Zanetti", "Lugano", "Zürich", "08:00", (1, 3)),
    _drive("Marco Zanetti", "Zürich", "Lugano", "16:30", (1, 3)),
    _drive("Laura Pianezzi", "Bellinzona", "Zürich", "17:30", (4,)),
    _drive("Laura Pianezzi", "Zürich", "Bellinzona", "17:30", SUN),
    _drive("Moreno Tognola", "Bellinzona", "Chur", "07:30", (0, 2, 4)),
    _drive("Moreno Tognola", "Chur", "Bellinzona", "17:00", (0, 2, 4)),
    # Weekends.
    _drive("Roberto Ghisletta", "Lugano", "Airolo", "08:30", SAT),
    _drive("Roberto Ghisletta", "Airolo", "Lugano", "17:00", SUN),
    # --- Graubünden --------------------------------------------------------
    *_commute("Gian Caduff", "Chur", "Zürich", "06:45", "17:30"),
    *_commute("Ursina Cavelti", "Davos", "Chur", "07:00", "17:30"),
    *_commute("Men Cadonau", "Scuol", "St. Moritz", "07:10", "17:20"),
    *_commute("Ladina Bezzola", "St. Moritz", "Chur", "07:30", "17:00", weekdays=(0, 2, 4)),
    *_commute("Flurin Casutt", "Ilanz", "Chur", "07:15", "17:30"),
    *_commute("Corsin Deplazes", "Disentis/Mustér", "Chur", "06:50", "17:10", seats=2),
    *_commute("Seraina Joos", "Thusis", "Chur", "07:20", "17:15"),
    *_commute("Andri Gaudenz", "Arosa", "Chur", "07:30", "18:00", seats=2),
    *_commute("Nina Bundi", "Landquart", "Chur", "07:40", "17:00"),
    *_commute("Mirco Lardi", "Poschiavo", "St. Moritz", "07:00", "17:30", weekdays=(0, 2, 4)),
    *_commute("Rolf Kühne", "Sargans", "Chur", "07:20", "17:10"),
    *_commute("Reto Clavadetscher", "Klosters", "Landquart", "07:10", "17:20"),
    _drive("Martin Hartmann", "Chur", "Davos", "11:00", DAILY),
    _drive("Martin Hartmann", "Davos", "Chur", "14:00", DAILY),
    _drive("Claudio Pally", "Chur", "Disentis/Mustér", "12:30", DAILY),
    _drive("Claudio Pally", "Disentis/Mustér", "Chur", "15:30", DAILY),
    _drive("Silvia Item", "Chur", "St. Moritz", "10:30", (5, 6)),
    _drive("Silvia Item", "St. Moritz", "Chur", "16:00", (5, 6)),
    _drive("Gianna Clalüna", "St. Moritz", "Scuol", "12:00", (1, 3, 5)),
    _drive("Gianna Clalüna", "Scuol", "St. Moritz", "15:30", (1, 3, 5)),
    _drive("Duri Puorger", "Davos", "Scuol", "09:00", SAT),
    _drive("Duri Puorger", "Scuol", "Davos", "16:30", SUN),
    # Weekends from the cities to the mountains.
    _drive("Thomas Schällibaum", "Zürich", "Davos", "07:30", SAT, seats=4),
    _drive("Thomas Schällibaum", "Davos", "Zürich", "16:30", SUN, seats=4),
    _drive("Petra Vogel", "Zürich", "Flims", "08:00", SAT),
    _drive("Petra Vogel", "Flims", "Zürich", "16:00", SUN),
    _drive("Jan Zogg", "St. Gallen", "Chur", "08:30", SAT),
    _drive("Jan Zogg", "Chur", "St. Gallen", "17:30", SUN),
]

# --- Generated drivers -----------------------------------------------------
# Many more commuters from the smaller towns, and more departures on the busy
# corridors. Names, times and seats are drawn at random but seeded by the
# trip, so they stay the same from one run to the next and adding a trip
# doesn't change the others.

FIRST_NAMES = {
    "fr": [
        "Antoine", "Aurélie", "Baptiste", "Céline", "Christophe", "Delphine", "Émilie",
        "Fabien", "Florence", "Guillaume", "Hélène", "Jérôme", "Joëlle", "Laurent",
        "Manon", "Mathieu", "Nathalie", "Noémie", "Patrick", "Romain", "Sandrine",
        "Sébastien", "Valérie", "Vincent", "Yann", "Chloé", "Benoît", "Martine",
        "Cédric", "Gaëlle", "Maxime", "Océane", "Grégoire", "Léa", "Arnaud", "Inès",
        "Alain", "Anne-Laure", "Bernard", "Camille", "Caroline", "Damien", "David",
        "Didier", "Estelle", "Frédéric", "Gilles", "Isabelle", "Jacques", "Julie",
        "Justine", "Loïc", "Marion", "Mélissa", "Olivier", "Pascale", "Raphaël",
        "Sophie", "Thierry", "Virginie",
    ],
    "de": [
        "Andreas", "Barbara", "Christian", "Corinne", "Daniel", "Esther", "Fabian",
        "Franziska", "Hans", "Jasmin", "Katrin", "Markus", "Michael", "Monika",
        "Patrick", "Regula", "Roger", "Sabine", "Samuel", "Stefanie", "Thomas",
        "Ueli", "Vreni", "Yvonne", "Adrian", "Bettina", "Dominik", "Karin", "Lea",
        "Manuel", "Nicole", "Pascal", "Rahel", "Severin", "Tanja", "Livia", "Beat",
        "Benjamin", "Brigitte", "Cornelia", "David", "Elias", "Erika", "Fritz",
        "Gabriela", "Heidi", "Irene", "Jan", "Jonas", "Judith", "Kurt", "Lars",
        "Lena", "Lukas", "Marcel", "Mirjam", "Nadja", "Noah", "Olivia", "Philipp",
        "Raphael", "Ruth", "Sandra", "Silvan", "Simone", "Stefan", "Sven", "Urs",
        "Werner", "Zoe",
    ],
    "it": [
        "Alessandro", "Barbara", "Claudio", "Cristina", "Daniele", "Federica",
        "Francesca", "Gabriele", "Ilaria", "Lorenzo", "Luca", "Manuela", "Massimo",
        "Michela", "Nadia", "Paolo", "Riccardo", "Roberta", "Silvia", "Simone",
        "Stefano", "Valentina", "Andrea", "Mattia", "Giada", "Fabrizio", "Elisa",
        "Diego", "Martina", "Gianluca", "Alberto", "Anna", "Carlo", "Chiara", "Dario",
        "Elena", "Fabio", "Giorgio", "Giulia", "Laura", "Marco", "Monica", "Nicola",
        "Patrizia", "Roberto", "Sara", "Sergio", "Tiziana", "Ivo", "Noemi",
    ],
    "gr": [
        "Gian", "Ursina", "Men", "Ladina", "Flurin", "Seraina", "Andri", "Curdin",
        "Mirjam", "Reto", "Annina", "Duri", "Mengia", "Not", "Chasper", "Barbla",
        "Linard", "Madlaina", "Jon", "Corina", "Marco", "Sandra", "Peter", "Nina",
        "Claudia", "Lukas", "Anna", "Mario", "Silvia", "Martin", "Aita", "Benedetg",
        "Cla", "Domenic", "Fadri", "Gianna", "Ivo", "Leta", "Mauro", "Nuot", "Riet",
        "Romana", "Selina", "Tumasch", "Valeria",
    ],
}

LAST_NAMES = {
    "fr": [
        "Berset", "Blanc", "Bourgeois", "Carrard", "Chevalley", "Clément", "Cornaz",
        "Délèze", "Ducret", "Fournier", "Gay", "Girard", "Jaccard", "Jaquet",
        "Liechti", "Magnin", "Maillard", "Mercier", "Michellod", "Monnier", "Pasche",
        "Perrin", "Python", "Reymond", "Rey", "Richard", "Rossier", "Schaller",
        "Tissot", "Vuilleumier", "Zufferey", "Bonvin", "Crettenand", "Droz",
        "Gindrat", "Humbert-Droz", "Aebischer", "Barras", "Baud", "Bersier", "Bonny",
        "Borcard", "Chappuis", "Chollet", "Collaud", "Cotting", "Dénervaud", "Dubey",
        "Ecoffey", "Favez", "Gachet", "Genoud", "Grandjean", "Jordan", "Kolly",
        "Maret", "Mauron", "Moret", "Nicolet", "Overney", "Pittet", "Progin",
        "Ruffieux", "Savary", "Sottas", "Theytaz", "Vonlanthen", "Vuignier", "Wicht",
        "Yerly",
    ],
    "de": [
        "Ammann", "Bachmann", "Baumgartner", "Brändli", "Egli", "Fischer", "Fuchs",
        "Gisler", "Hofer", "Hofmann", "Iten", "Kaufmann", "Kunz", "Lehmann",
        "Lüthi", "Marti", "Moser", "Müller", "Nussbaumer", "Rüegg", "Schneider",
        "Schwarz", "Sommer", "Stalder", "Suter", "Tanner", "Vogt", "Weber",
        "Widmer", "Zaugg", "Zimmermann", "Arnold", "Betschart", "Fässler", "Imhof",
        "Wyrsch", "Aebi", "Bieri", "Blaser", "Bösch", "Bucher", "Burri",
        "Eggenberger", "Flückiger", "Frey", "Gasser", "Gerber", "Gloor", "Haller",
        "Hess", "Hug", "Hürlimann", "Jost", "Kälin", "Keller", "Koch", "Kuhn",
        "Leuenberger", "Locher", "Maurer", "Meier", "Merz", "Niederberger", "Ott",
        "Pfister", "Reber", "Ritter", "Roth", "Schär", "Scherrer", "Schmid",
        "Schürch", "Sigrist", "Steiner", "Stucki", "Studer", "Wüthrich", "Wirth",
        "Wyss", "Zbinden",
    ],
    "it": [
        "Balestra", "Bassi", "Beltrami", "Bernasconi", "Bianchi", "Bottani",
        "Canonica", "Cavadini", "Colombo", "Conti", "Cereghetti", "Dotta",
        "Ferrazzini", "Galli", "Gianella", "Guidotti", "Lafranchi", "Malè",
        "Maggetti", "Molinari", "Pellandini", "Quadri", "Righetti", "Sala",
        "Soldati", "Storni", "Tamò", "Vanoni", "Zanini", "Zappa", "Albertoni",
        "Antognini", "Bernardoni", "Borradori", "Bulloni", "Camponovo", "Croce",
        "Fontana", "Gendotti", "Lepori", "Lurati", "Martinoli", "Pedrazzini", "Polli",
        "Rezzonico", "Rusconi", "Sartori", "Tognola", "Valsangiacomo", "Zanetti",
    ],
    "gr": [
        "Bergamin", "Cadruvi", "Camenisch", "Capaul", "Casanova", "Caviezel",
        "Cathomen", "Clavadetscher", "Collenberg", "Darms", "Derungs", "Dosch",
        "Florin", "Hassler", "Jenal", "Juon", "Luzi", "Mani", "Mengelt", "Nay",
        "Pinggera", "Riedi", "Sialm", "Schmid", "Tscharner", "Tuor", "Vieli",
        "Brunold", "Gartmann", "Bisaz", "Arpagaus", "Beeli", "Cadonau", "Candinas",
        "Cavelti", "Cantieni", "Defila", "Gadient", "Janett", "Lardi", "Monn",
        "Padrun", "Rauch", "Sutter", "Tomaschett",
    ],
}


def _language(place):
    if place in FRENCH_SPEAKING:
        return "fr"
    if place in ITALIAN_SPEAKING:
        return "it"
    if place in GRAUBUNDEN:
        return "gr"
    return "de"


_taken_names = {spec["driver_name"] for spec in DEMO_DRIVES}


def _new_driver(rng, place):
    """A name not used by any other demo driver, typical of `place`."""
    language = _language(place)
    while True:
        name = f"{rng.choice(FIRST_NAMES[language])} {rng.choice(LAST_NAMES[language])}"
        if name not in _taken_names:
            _taken_names.add(name)
            return name


def _clock(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _generated_commutes(home, work, count):
    """`count` commuters from home to work, leaving between 06:00 and 08:55 and
    coming back between 15:30 and 19:25; a few work part-time."""
    drives = []
    for n in range(count):
        rng = random.Random(f"commute {home} > {work} #{n}")
        driver = _new_driver(rng, home)
        out = _clock(6 * 60 + 5 * rng.randrange(36))
        back = _clock(15 * 60 + 30 + 5 * rng.randrange(48))
        weekdays = MON_FRI if rng.random() < 0.8 else rng.choice(
            [(0, 1, 2, 3), (1, 2, 3, 4), (0, 2, 4), (0, 1, 3)]
        )
        drives += _commute(driver, home, work, out, back, weekdays, rng.choice((1, 2, 2, 3, 3, 3, 4)))
    return drives


def _generated_corridor(a, b, times, weekdays=DAILY):
    """One driver each way at each of `times`."""
    drives = []
    for origin, destination in ((a, b), (b, a)):
        for at in times:
            rng = random.Random(f"corridor {origin} > {destination} @{at}")
            driver = _new_driver(rng, origin)
            drives.append(_drive(driver, origin, destination, at, weekdays, rng.choice((1, 2, 3, 3, 4))))
    return drives


# Commuters per unit of COMMUTES' sizes.
COMMUTER_SCALE = 14

# (home, work, size): COMMUTER_SCALE x size commuters on the trip.
COMMUTES = [
    # Romandie
    ("Morges", "Lausanne", 3), ("Rolle", "Lausanne", 1), ("Rolle", "Genève", 2),
    ("Gland", "Genève", 2), ("Nyon", "Lausanne", 1), ("Renens", "Genève", 1),
    ("Échallens", "Lausanne", 2), ("Moudon", "Lausanne", 2), ("Payerne", "Lausanne", 1),
    ("Payerne", "Fribourg", 2), ("Estavayer-le-Lac", "Fribourg", 1),
    ("Estavayer-le-Lac", "Yverdon-les-Bains", 1), ("Romont", "Fribourg", 2),
    ("Romont", "Lausanne", 1), ("Bulle", "Fribourg", 3), ("Châtel-Saint-Denis", "Vevey", 1),
    ("Châtel-Saint-Denis", "Lausanne", 2), ("Vevey", "Lausanne", 3), ("Montreux", "Lausanne", 2),
    ("Aigle", "Lausanne", 2), ("Aigle", "Montreux", 1), ("Monthey", "Lausanne", 1),
    ("Monthey", "Martigny", 1), ("Monthey", "Vevey", 1), ("Bex", "Monthey", 1),
    ("Martigny", "Sion", 2), ("Verbier", "Martigny", 1), ("Crans-Montana", "Sierre", 1),
    ("Crans-Montana", "Sion", 1), ("Sainte-Croix", "Yverdon-les-Bains", 1),
    ("Yverdon-les-Bains", "Neuchâtel", 2), ("Le Locle", "La Chaux-de-Fonds", 2),
    ("Le Locle", "Neuchâtel", 1), ("Saint-Imier", "Biel/Bienne", 1),
    ("Saint-Imier", "La Chaux-de-Fonds", 1), ("Moutier", "Biel/Bienne", 1),
    ("Moutier", "Delémont", 1), ("Porrentruy", "Delémont", 2), ("Porrentruy", "Basel", 1),
    ("Delémont", "Basel", 2), ("Fribourg", "Bern", 3), ("Neuchâtel", "Lausanne", 1),
    ("Sion", "Martigny", 1),
    # German-speaking Switzerland
    ("Visp", "Brig", 1), ("Visp", "Sion", 1), ("Murten", "Bern", 2), ("Murten", "Fribourg", 1),
    ("Lyss", "Bern", 2), ("Lyss", "Biel/Bienne", 1), ("Grenchen", "Biel/Bienne", 1),
    ("Grenchen", "Solothurn", 1), ("Burgdorf", "Bern", 3), ("Langnau im Emmental", "Bern", 2),
    ("Langenthal", "Bern", 1), ("Langenthal", "Olten", 1), ("Zofingen", "Olten", 1),
    ("Zofingen", "Aarau", 1), ("Spiez", "Bern", 2), ("Spiez", "Thun", 1),
    ("Frutigen", "Thun", 1), ("Interlaken", "Thun", 2), ("Meiringen", "Interlaken", 1),
    ("Grindelwald", "Interlaken", 1), ("Zweisimmen", "Thun", 1), ("Gstaad", "Zweisimmen", 1),
    ("Liestal", "Basel", 3), ("Rheinfelden", "Basel", 2), ("Laufen", "Basel", 2),
    ("Aarau", "Zürich", 2), ("Lenzburg", "Zürich", 1), ("Lenzburg", "Aarau", 1),
    ("Wohlen", "Zürich", 1), ("Baden", "Zürich", 3), ("Brugg", "Baden", 1),
    ("Brugg", "Zürich", 1), ("Dietikon", "Zürich", 1), ("Bülach", "Zürich", 2),
    ("Kloten", "Winterthur", 1), ("Uster", "Zürich", 2), ("Wetzikon", "Zürich", 2),
    ("Rapperswil", "Zürich", 2), ("Horgen", "Zug", 1), ("Meilen", "Zürich", 1),
    ("Pfäffikon SZ", "Zürich", 1), ("Einsiedeln", "Pfäffikon SZ", 1), ("Schwyz", "Zug", 1),
    ("Schwyz", "Luzern", 1), ("Altdorf", "Luzern", 1), ("Altdorf", "Schwyz", 1),
    ("Stans", "Luzern", 2), ("Sarnen", "Luzern", 2), ("Engelberg", "Stans", 1),
    ("Sursee", "Luzern", 2), ("Zug", "Luzern", 1), ("Frauenfeld", "Winterthur", 2),
    ("Frauenfeld", "Zürich", 1), ("Wil", "St. Gallen", 2), ("Wil", "Winterthur", 1),
    ("Weinfelden", "Frauenfeld", 1), ("Kreuzlingen", "Frauenfeld", 1),
    ("Kreuzlingen", "St. Gallen", 1), ("Romanshorn", "St. Gallen", 1), ("Arbon", "St. Gallen", 2),
    ("Rorschach", "St. Gallen", 1), ("Herisau", "St. Gallen", 2), ("Appenzell", "St. Gallen", 1),
    ("Wattwil", "St. Gallen", 1), ("Wattwil", "Rapperswil", 1), ("Buchs SG", "Sargans", 1),
    ("Buchs SG", "St. Gallen", 1), ("Glarus", "Rapperswil", 1), ("Glarus", "Zürich", 1),
    ("Schaffhausen", "Winterthur", 1),
    # Ticino and Italian-speaking Graubünden
    ("Ascona", "Locarno", 2), ("Ascona", "Bellinzona", 1), ("Brissago", "Locarno", 1),
    ("Maggia", "Locarno", 1), ("Gordola", "Bellinzona", 1), ("Gordola", "Lugano", 1),
    ("Giubiasco", "Lugano", 2), ("Faido", "Bellinzona", 2), ("Acquarossa", "Bellinzona", 1),
    ("Olivone", "Biasca", 1), ("Biasca", "Bellinzona", 2), ("Tesserete", "Lugano", 2),
    ("Agno", "Lugano", 1), ("Caslano", "Lugano", 1), ("Melide", "Lugano", 1),
    ("Stabio", "Lugano", 1), ("Stabio", "Mendrisio", 1), ("Chiasso", "Mendrisio", 1),
    ("Mendrisio", "Bellinzona", 1), ("Locarno", "Lugano", 1), ("Lugano", "Locarno", 1),
    ("Rivera", "Lugano", 1), ("Grono", "Bellinzona", 1), ("Mesocco", "Bellinzona", 1),
    ("Brusio", "Poschiavo", 1),
    # Graubünden
    ("Domat/Ems", "Chur", 2), ("Bonaduz", "Chur", 1), ("Trimmis", "Chur", 1),
    ("Maienfeld", "Chur", 1), ("Maienfeld", "Sargans", 1), ("Schiers", "Landquart", 1),
    ("Schiers", "Chur", 1), ("Lenzerheide", "Chur", 2), ("Tiefencastel", "Chur", 1),
    ("Savognin", "Chur", 1), ("Andeer", "Thusis", 1), ("Splügen", "Thusis", 1),
    ("Laax", "Chur", 1), ("Flims", "Chur", 2), ("Trun", "Ilanz", 1), ("Vals", "Ilanz", 1),
    ("Bergün", "Chur", 1), ("Samedan", "St. Moritz", 2), ("Pontresina", "St. Moritz", 1),
    ("Silvaplana", "St. Moritz", 1), ("Zuoz", "Samedan", 1), ("Zernez", "Samedan", 1),
    ("Müstair", "Zernez", 1), ("Müstair", "Scuol", 1), ("Davos", "Klosters", 1),
    ("Klosters", "Chur", 1), ("Landquart", "Zürich", 1), ("Chur", "St. Gallen", 1),
    ("Thusis", "Chur", 1), ("Ilanz", "Flims", 1),
]

def _every(first, last, hours):
    """Departure times from `first` to `last` o'clock, every `hours` hours."""
    return [f"{h:02d}:00" for h in range(first, last + 1, hours)]


HOURLY = _every(8, 21, 1)
EVERY_TWO_HOURS = _every(9, 21, 2)

# (one end, other end, departure times each way): every day, on top of the
# commuters.
CORRIDORS = [
    # The busy ones.
    ("Lausanne", "Genève", HOURLY),
    ("Lausanne", "Fribourg", HOURLY),
    ("Fribourg", "Bern", HOURLY),
    ("Lausanne", "Sion", HOURLY),
    ("Neuchâtel", "Biel/Bienne", HOURLY),
    ("Bern", "Zürich", HOURLY),
    ("Bern", "Basel", HOURLY),
    ("Bern", "Thun", HOURLY),
    ("Zürich", "Basel", HOURLY),
    ("Zürich", "Luzern", HOURLY),
    ("Zürich", "St. Gallen", HOURLY),
    ("Zürich", "Winterthur", HOURLY),
    ("Lugano", "Bellinzona", HOURLY),
    ("Lugano", "Locarno", HOURLY),
    ("Lugano", "Chiasso", HOURLY),
    ("Bellinzona", "Biasca", HOURLY),
    ("Chur", "Landquart", HOURLY),
    ("Chur", "Davos", HOURLY),
    ("Chur", "Lenzerheide", HOURLY),
    ("Chur", "Zürich", HOURLY),
    ("St. Moritz", "Samedan", HOURLY),
    # The others.
    ("Genève", "Nyon", EVERY_TWO_HOURS),
    ("Lausanne", "Yverdon-les-Bains", EVERY_TWO_HOURS),
    ("Lausanne", "Vevey", EVERY_TWO_HOURS),
    ("Montreux", "Martigny", EVERY_TWO_HOURS),
    ("Sion", "Sierre", EVERY_TWO_HOURS),
    ("Neuchâtel", "La Chaux-de-Fonds", EVERY_TWO_HOURS),
    ("Bern", "Biel/Bienne", EVERY_TWO_HOURS),
    ("Thun", "Interlaken", EVERY_TWO_HOURS),
    ("Basel", "Olten", EVERY_TWO_HOURS),
    ("Aarau", "Olten", EVERY_TWO_HOURS),
    ("Zürich", "Zug", EVERY_TWO_HOURS),
    ("Luzern", "Zug", EVERY_TWO_HOURS),
    ("Winterthur", "St. Gallen", EVERY_TWO_HOURS),
    ("Bellinzona", "Locarno", EVERY_TWO_HOURS),
    ("Lugano", "Mendrisio", EVERY_TWO_HOURS),
    ("Chur", "Thusis", EVERY_TWO_HOURS),
    ("Chur", "Flims", EVERY_TWO_HOURS),
    ("Davos", "Klosters", EVERY_TWO_HOURS),
    ("St. Moritz", "Pontresina", EVERY_TWO_HOURS),
    ("Brig", "Visp", EVERY_TWO_HOURS),
]

for _home, _work, _size in COMMUTES:
    DEMO_DRIVES += _generated_commutes(_home, _work, COMMUTER_SCALE * _size)
for _a, _b, _times in CORRIDORS:
    DEMO_DRIVES += _generated_corridor(_a, _b, _times)


def _departure_iso(dt: datetime) -> str:
    return dt.isoformat(timespec="minutes")


def _expected(now: datetime) -> dict:
    """(driver, departure time) -> spec, for every demo drive due in the window."""
    expected = {}
    for day in range(DAYS_AHEAD):
        date = now.date() + timedelta(days=day)
        for spec in DEMO_DRIVES:
            departure = datetime.combine(date, spec["time"])
            if date.weekday() in spec["weekdays"] and departure > now:
                expected[(spec["driver_name"], _departure_iso(departure))] = spec
    return expected


def _route(spec, cache: dict) -> dict:
    """Road route for a template's trip: the one stored in demo_routes when
    there is one, so the routing server is only asked once per trip."""
    key = (spec["origin"]["name"], spec["destination"]["name"])
    if key not in cache:
        cache[key] = db.find_demo_route(*key)
    if cache[key] is None:
        cache[key] = matching.route_with_fallback([
            (spec["origin"]["lat"], spec["origin"]["lon"]),
            (spec["destination"]["lat"], spec["destination"]["lon"]),
        ])
        if cache[key]["source"] == "osrm":
            db.save_demo_route(*key, cache[key])
            clock.sleep(ROUTING_PAUSE_S)
    return cache[key]


# Drives are written in batches: one transaction each, short enough not to
# hold up visitors posting a drive for long.
BATCH_SIZE = 1000


def refresh(now: datetime | None = None) -> tuple[int, int]:
    """Bring demo drives up to date; returns (deleted, created)."""
    db.init_db()
    now = now or datetime.now(LOCAL_TZ).replace(tzinfo=None)
    deleted = db.delete_demo_drives_before(_departure_iso(now))
    expected = _expected(now)

    # Upcoming demo drives from templates that changed or were removed.
    existing, outdated = set(), []
    for d in db.list_drives(upcoming_only=False, with_geometry=False):
        if not d["is_demo"]:
            continue
        spec = expected.get((d["driver_name"], d["departure_time"]))
        if (
            spec
            and spec["origin"]["name"] == d["origin_name"]
            and spec["destination"]["name"] == d["destination_name"]
        ):
            existing.add((d["driver_name"], d["departure_time"]))
        else:
            outdated.append(d["id"])
    for i in range(0, len(outdated), BATCH_SIZE):
        deleted += db.delete_drives(outdated[i:i + BATCH_SIZE])

    created = 0
    routes, batch = {}, []
    for (driver_name, departure_time), spec in expected.items():
        if (driver_name, departure_time) in existing:
            continue
        route = _route(spec, routes)
        batch.append({
            "driver_name": driver_name,
            "origin_name": spec["origin"]["name"],
            "origin_lat": spec["origin"]["lat"],
            "origin_lon": spec["origin"]["lon"],
            "destination_name": spec["destination"]["name"],
            "destination_lat": spec["destination"]["lat"],
            "destination_lon": spec["destination"]["lon"],
            "departure_time": departure_time,
            "seats_available": spec["seats_available"],
            # A road route is shared through demo_routes; only a straight-line
            # fallback (a few points) is kept on the drive.
            "route_geometry": None if route["source"] == "osrm" else route["geometry"],
            "route_distance_m": route["distance_m"],
            "route_duration_s": route["duration_s"],
            "route_source": route["source"],
            "is_demo": True,
        })
        if len(batch) == BATCH_SIZE:
            created += db.create_drives(batch)
            batch = []
    created += db.create_drives(batch)
    return deleted, created


if __name__ == "__main__":
    deleted, created = refresh()
    print(f"Demo drives: {deleted} deleted, {created} created")
