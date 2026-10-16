"""The 32 teams at their 30 lower-48 stadiums (2026 season), for Turf Wars: every NFL stadium.

STADIUMS: stadium code (cache/stores.csv `chain`) -> the teams that play there. MetLife (Giants,
Jets) and SoFi (Rams, Chargers) are shared: one region each, drawn in the two teams' colours as
stripes (Max, 2026-10-16), both logos inside.
TEAMS: team -> (name, primary, secondary, ESPN logo file). Colours are the teams' published
primary/secondary; the reel lightens dark ones for the black map and switches a team to its
secondary when a neighbour's colour is too close (Max: "similar to the team color unless there
are 2 very similar colors that collide").
"""

STADIUMS = {
    "ARI": ["ARI"], "ATL": ["ATL"], "BAL": ["BAL"], "BUF": ["BUF"], "CAR": ["CAR"], "CHI": ["CHI"],
    "CIN": ["CIN"], "CLE": ["CLE"], "DAL": ["DAL"], "DEN": ["DEN"], "DET": ["DET"], "GB": ["GB"],
    "HOU": ["HOU"], "IND": ["IND"], "JAX": ["JAX"], "KC": ["KC"], "LV": ["LV"], "LA": ["LAR", "LAC"],
    "MIA": ["MIA"], "MIN": ["MIN"], "NE": ["NE"], "NO": ["NO"], "NY": ["NYG", "NYJ"], "PHI": ["PHI"],
    "PIT": ["PIT"], "SF": ["SF"], "SEA": ["SEA"], "TB": ["TB"], "TEN": ["TEN"], "WAS": ["WAS"],
}

TEAMS = {
    "ARI": ("Cardinals", "#97233F", "#FFB612", "ari"), "ATL": ("Falcons", "#A71930", "#A5ACAF", "atl"),
    "BAL": ("Ravens", "#241773", "#9E7C0C", "bal"), "BUF": ("Bills", "#00338D", "#C60C30", "buf"),
    "CAR": ("Panthers", "#0085CA", "#BFC0BF", "car"), "CHI": ("Bears", "#0B162A", "#C83803", "chi"),
    "CIN": ("Bengals", "#FB4F14", "#FFFFFF", "cin"), "CLE": ("Browns", "#311D00", "#FF3C00", "cle"),
    "DAL": ("Cowboys", "#003594", "#869397", "dal"), "DEN": ("Broncos", "#FB4F14", "#002244", "den"),
    "DET": ("Lions", "#0076B6", "#B0B7BC", "det"), "GB": ("Packers", "#203731", "#FFB612", "gb"),
    "HOU": ("Texans", "#03202F", "#A71930", "hou"), "IND": ("Colts", "#002C5F", "#A2AAAD", "ind"),
    "JAX": ("Jaguars", "#006778", "#D7A22A", "jax"), "KC": ("Chiefs", "#E31837", "#FFB81C", "kc"),
    "LV": ("Raiders", "#A5ACAF", "#000000", "lv"), "LAR": ("Rams", "#003594", "#FFA300", "lar"),
    "LAC": ("Chargers", "#0080C6", "#FFC20E", "lac"), "MIA": ("Dolphins", "#008E97", "#FC4C02", "mia"),
    "MIN": ("Vikings", "#4F2683", "#FFC62F", "min"), "NE": ("Patriots", "#002244", "#C60C30", "ne"),
    "NO": ("Saints", "#D3BC8D", "#101820", "no"), "NYG": ("Giants", "#0B2265", "#A71930", "nyg"),
    "NYJ": ("Jets", "#125740", "#FFFFFF", "nyj"), "PHI": ("Eagles", "#004C54", "#A5ACAF", "phi"),
    "PIT": ("Steelers", "#FFB612", "#101820", "pit"), "SF": ("49ers", "#AA0000", "#B3995D", "sf"),
    "SEA": ("Seahawks", "#002244", "#69BE28", "sea"), "TB": ("Buccaneers", "#D50A0A", "#FF7900", "tb"),
    "TEN": ("Titans", "#0C2340", "#4B92DB", "ten"), "WAS": ("Commanders", "#5A1414", "#FFB612", "wsh"),
}
