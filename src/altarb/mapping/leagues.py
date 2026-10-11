"""Which Polymarket US league codes correspond to each Kalshi game-winner series.

The matcher only compares games inside a mapped league, so (for example) NBA
Philadelphia is never paired with NHL Philadelphia, and men's La Liga is never
paired with women's Liga F even though the club names are identical.

Derived on 2026-10-10 by matching team names across both venues' open games;
each entry is the code that matched consistently. Add a series here once you've
confirmed its Polymarket code. Polymarket slugs look like
`aec-<code>-<teams>-<date>` (two-way) or `atc-<code>-<teams>-<date>-<outcome>`
(three-way, e.g. soccer).
"""

LEAGUES: dict[str, frozenset[str]] = {
    series: frozenset(codes.split())
    for series, codes in {
        # US major leagues
        "KXNFLGAME": "nfl",
        "KXNBAGAME": "nba",
        "KXNHLGAME": "nhl",
        "KXMLBGAME": "mlb",
        "KXMLSGAME": "mls",
        "KXNCAAFGAME": "cfb",
        "KXAHLGAME": "ahl",
        "KXUSLGAME": "uslc",
        # Soccer: Europe
        "KXEPLGAME": "epl",
        "KXEFLCHAMPIONSHIPGAME": "eflch",
        "KXEFLL1GAME": "efl1",
        "KXLALIGAGAME": "lal",
        "KXLALIGA2GAME": "lal2",
        "KXSERIEAGAME": "sea",
        "KXSERIEBGAME": "srb",
        "KXSERIECGAME": "serca sercb sercc",  # Serie C is split into three groups
        "KXBUNDESLIGAGAME": "bun",
        "KXBUNDESLIGA2GAME": "bun2",
        "KXLIGUE1GAME": "lg1",
        "KXLIGUE2GAME": "lig2",
        "KXEREDIVISIEGAME": "ere",
        "KXLIGAPORTUGALGAME": "ligpor",
        "KXBELGIANPLGAME": "bel1",
        "KXSCOTTISHPREMGAME": "scp",
        "KXSWISSLEAGUEGAME": "swsl",
        "KXSUPERLIGGAME": "tsl",
        "KXTFF1LIGGAME": "tff1",
        "KXSLGREECEGAME": "grsl",
        "KXEKSTRAKLASAGAME": "ekst",
        "KXALLSVENSKANGAME": "alsv",
        "KXELITESERIENGAME": "els",
        "KXDENSUPERLIGAGAME": "sld",
        "KXCZEFLGAME": "flc",
        "KXCZEFNLGAME": "fnl",
        "KXSVK2LGAME": "svk2",
        "KXSVNPLGAME": "svnp",
        "KXHNLGAME": "hnl",
        "KXUCLGAME": "ucl",
        "KXUELGAME": "uel",
        "KXUECLGAME": "uecl",
        "KXFIFAWGAME": "uwwcq",
        # Soccer: Americas
        "KXLIGAMXGAME": "lmx",
        "KXLIGAEXPGAME": "lexp",
        "KXBRASILEIROGAME": "bra",
        "KXBRASILEIROBGAME": "brb",
        "KXBRASILEIROCGAME": "brc",
        "KXARGPREMDIVGAME": "lpa",
        "KXARGNACBGAME": "arg2",
        "KXDIMAYORGAME": "lco",
        "KXECULPGAME": "ecu1",
        "KXURYPDGAME": "uru1",
        "KXAPFDDHGAME": "par1",
        "KXBOLPDIVGAME": "lpb",
        "KXPERLIGA1GAME": "pl1",
        # Soccer: Asia / Africa / Middle East
        "KXJLEAGUEGAME": "j1",
        "KXJ2LEAGUEGAME": "j2",
        "KXKLEAGUEGAME": "kl1",
        "KXTHAIL1GAME": "thai1",
        "KXIDNSLGAME": "idnsl",
        "KXSAUDIPLGAME": "spl",
        "KXEGYPLGAME": "egpl",
        "KXAFCCLGAME": "acle",
        # Basketball (non-US)
        "KXEUROLEAGUEGAME": "eurolg",
        "KXEUROCUPGAME": "eurocup",
        "KXABAGAME": "aba",
        "KXACBGAME": "acb",
        "KXBBLGAME": "bbl",
        "KXBSLGAME": "bsl",
        "KXBBSERIEAGAME": "lba",
        "KXBBSERIEA2GAME": "ita2",
        "KXLNBELITEGAME": "lnb",
        "KXLNBPGAME": "lnbp",
        "KXLKLGAME": "lkl",
        "KXNBLGAME": "nbl",
        "KXSLBGAME": "slb",
        "KXSBLGAME": "svkbl",
        "KXVTBGAME": "vtb",
        "KXCZENBLGAME": "cznbl",
        "KXAUTBSLGAME": "autbl",
        # Hockey (non-US)
        "KXKHLGAME": "khl",
        "KXDELGAME": "del",
        "KXNLGAME": "snhl",
        # Baseball (non-US)
        "KXKBOGAME": "kbo",
        # Esports
        "KXCS2GAME": "cs2",
        "KXLOLGAME": "lol",
        "KXOWGAME": "ow",
        "KXVALORANTGAME": "valorant",
        "KXDOTA2GAME": "dota2",
        "KXR6GAME": "r6",
        # Other
        "KXENGNLGAME": "engnl",
        "KXCHLLDPGAME": "pdc",
    }.items()
}
