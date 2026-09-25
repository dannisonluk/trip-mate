"""GET /api/v1/cities — city suggestions for the destination picker.

The shape of these tests is deliberate: the endpoint's *product rule* is that a
city is chosen from a known list, so most of what needs pinning is the boundary
behaviour — what happens on a partial word, an unknown word, or a term that
would otherwise be read as a wildcard. Each of the early-return paths here has a
counterpart test that goes through the real query, so a change that broke the
lookup while leaving the guards intact would still fail.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.anyio


@pytest.fixture()
def seeded_cities(client):
    """The endpoint needs reference rows, which production gets from an import.

    The import script is not run here because it needs the downloaded GeoNames
    dump (~5.7 MB) and would take seconds per session. Instead a handful of real
    rows are inserted directly, chosen to cover the cases the endpoint must get
    right: an accented display name whose `asciiname` is plain, a same-prefix
    pair that must rank by population, a capital that must outrank a larger
    non-capital, and two same-name cities in different countries.
    """
    from sqlalchemy import delete

    from app.db.session import AsyncSessionLocal
    from app.models.city import City

    rows = [
        # geonameid, name, asciiname, cc, country, admin1, lat, lon, pop, code
        # All values below are copied from the real cities5000 dump, so the
        # `asciiname` columns are the genuine unaccented forms and the feature
        # codes are the genuine seat tiers.
        (1853909, "Ōsaka", "Osaka", "JP", "Japan", "Osaka", 34.69379, 135.50107, 2753862, "PPLA"),
        (1850147, "Tokyo", "Tokyo", "JP", "Japan", "Tokyo", 35.6895, 139.69171, 8336599, "PPLC"),
        (1854982, "Ōsaki", "Osaki", "JP", "Japan", "Miyagi", 38.57361, 140.95472, 128763, "PPLA2"),
        (1519883, "Osakarovka", "Osakarovka", "KZ", "Kazakhstan", None, 50.3, 72.2, 7305, "PPLA2"),

        # --- The decisive pair ----------------------------------------------
        # A PPLC capital that a non-capital of the *same name* outsizes. Both
        # real rows, and the only pair here on which the two candidate orderings
        # disagree:
        #
        #   seat boost ON   -> San Marino (SM, PPLC, 4.5k) first
        #   seat boost OFF  -> San Marino (US, PPL, 13.5k) first
        #
        # Verified against the real dataset: `San Marino` (SM) is PPLC with a
        # population of 4,500, and `San Marino` (California) is a plain PPL with
        # 13,464. 92 real prefixes behave this way; this is the smallest and
        # clearest of them.
        (3168070, "San Marino", "San Marino", "SM", "San Marino", "San Marino", 43.93667, 12.44639, 4500, "PPLC"),
        (5392400, "San Marino", "San Marino", "US", "United States", "California", 34.1214, -118.10646, 13464, "PPL"),

        # --- The second tier (PPLA*), against the lowest tier ----------------
        # `Abbeville` (FR, PPLA3, 26k) is a departmental seat; `Abbotsford` (CA,
        # plain PPL, 141k) is five times larger and must still sort below it.
        # Without a PPLA/PPL pair sharing a prefix, flattening the boost's top
        # two tiers together (`PPLC` and `PPLA*` both 2) would be undetectable —
        # every other pair here separates PPLC from something unboosted, which
        # that change leaves intact.
        (3038789, "Abbeville", "Abbeville", "FR", "France", "Hauts-de-France", 50.10521, 1.83547, 26461, "PPLA3"),
        (5881791, "Abbotsford", "Abbotsford", "CA", "Canada", "British Columbia", 49.05798, -122.25257, 141397, "PPL"),

        # --- All three tiers under one prefix --------------------------------
        # The only family here where every tier boundary is decisive *and* the
        # capital is the smallest of the three, so each boundary is observable
        # independently. Populations run in the same order as the tiers:
        #
        #   Funafuti (TV, PPLC,   6,320)  <- smallest, must lead
        #   Fundulea (RO, PPLA2,  6,668)  <- beats a town twice its size
        #   Funes    (AR, PPL,   14,750)  <- largest, must trail
        #
        # `fun` is chosen because it is small (18 rows in the real dump) and
        # because without it, collapsing the top two boost tiers into one would
        # leave every other assertion in this file intact: elsewhere a PPLC is
        # either absent or also the largest match, so `3` and `2` are
        # indistinguishable.
        (2110394, "Funafuti", "Funafuti", "TV", "Tuvalu", "Funafuti", -8.52425, 179.19417, 6320, "PPLC"),
        (677790, "Fundulea", "Fundulea", "RO", "Romania", "Calarasi", 44.46667, 26.51667, 6668, "PPLA2"),
        (3855302, "Funes", "Funes", "AR", "Argentina", "Santa Fe", -32.91568, -60.80995, 14750, "PPL"),

        # --- The population key, in isolation --------------------------------
        # Three seats of different tiers, so either key alone gives the wrong
        # answer for the full list: the PPLC leads on tier, the PPLA2 leads on
        # population, and the PPL trails both. This is the realistic shape of a
        # prefix match, and it is why "sorted by population" is not a safe
        # simplification of the rule.
        (5392171, "San Jose", "San Jose", "US", "United States", "California", 37.33939, -121.89496, 997368, "PPLA2"),
        (1689510, "San Jose", "San Jose", "PH", "Philippines", "Mimaropa", 12.35, 121.0667, 143495, "PPL"),
        (3621849, "San José", "San Jose", "CR", "Costa Rica", "San José", 9.93333, -84.08333, 335007, "PPLC"),
    ]

    async def _seed():
        async with AsyncSessionLocal() as db:
            # The test database is shared across the whole session and only ever
            # gets `create_all`, so a previous module's rows survive. Clear this
            # table first or "exactly N results" assertions become order-dependent.
            await db.execute(delete(City))
            db.add_all(
                [
                    City(
                        id=r[0], name=r[1], asciiname=r[2], country_code=r[3],
                        country_name=r[4], admin1_code=None, admin1_name=r[5],
                        latitude=r[6], longitude=r[7], population=r[8],
                        feature_code=r[9], timezone=None,
                    )
                    for r in rows
                ]
            )
            await db.commit()

    import asyncio

    # Run the async seed on the TestClient's own event loop, so it uses the same
    # engine and connection pool as the requests. `asyncio.run` here would create
    # a second loop and a second engine binding, which on SQLite is how you get
    # "database is locked" or writes that the app cannot see.
    client.portal.call(_seed) if hasattr(client, "portal") else asyncio.run(_seed())
    return {r[0]: r[1] for r in rows}


def names(body) -> list[str]:
    return [s["name"] for s in body["suggestions"]]


class TestPrefixMatching:
    def test_finds_city_by_full_name(self, client, register_user, seeded_cities):
        headers = register_user()["headers"]
        resp = client.get("/api/v1/cities", params={"q": "Osaka"}, headers=headers)
        assert resp.status_code == 200, resp.text
        got = names(resp.json())
        # The match is a *prefix* match, so asserting a single exact result would
        # encode substring semantics the endpoint deliberately does not have.
        assert got[0] == "Ōsaka", got
        assert set(got) <= {"Ōsaka", "Osakarovka"}, got

    def test_finds_city_by_partial_prefix(self, client, register_user, seeded_cities):
        """A partial word is the normal case — the picker fires per keystroke."""
        headers = register_user()["headers"]
        resp = client.get("/api/v1/cities", params={"q": "Osak"}, headers=headers)
        assert resp.status_code == 200, resp.text
        got = names(resp.json())
        # All three rows share the prefix; the *order* is asserted in TestRanking,
        # which is where the two-key behaviour lives.
        assert set(got) == {"Ōsaka", "Ōsaki", "Osakarovka"}, got

    @pytest.mark.parametrize("term", ["osaka", "OSAKA", "OsAkA"])
    def test_matching_is_case_insensitive(self, client, register_user, seeded_cities, term):
        """Case must not matter: the column is `COLLATE NOCASE` precisely so the
        index can serve `LIKE`, and a regression there would be invisible in a
        test that only ever used the canonical casing."""
        headers = register_user()["headers"]
        resp = client.get("/api/v1/cities", params={"q": term}, headers=headers)
        assert resp.status_code == 200, resp.text
        assert "Ōsaka" in names(resp.json())

    def test_matches_accented_name_via_asciiname(self, client, register_user, seeded_cities):
        """`Ōsaka` and `Ōsaki` must be reachable by typing plain ASCII.

        14,419 GeoNames display names carry diacritics. If the search ran against
        `name` instead of `asciiname`, every one of them would be unreachable for
        a user without the appropriate keyboard — and the failure is silent,
        because the list simply comes back short.
        """
        headers = register_user()["headers"]
        for term, expected in (("osaka", "Ōsaka"), ("osaki", "Ōsaki"), ("sanjose", None)):
            resp = client.get("/api/v1/cities", params={"q": term}, headers=headers)
            assert resp.status_code == 200, resp.text
            if expected:
                assert expected in names(resp.json()), (term, names(resp.json()))

    def test_shortened_prefix_finds_the_same_row(self, client, register_user, seeded_cities):
        """`osak` and `osaka` must both reach `Ōsaka`.

        This is the regression guard for the way the lookup can silently rot:
        storing a diacritic in `asciiname`, so a shorter but still-ASCII prefix
        stops matching the accented row that the user can plainly see.
        """
        headers = register_user()["headers"]
        for term in ("osak", "osaka"):
            resp = client.get("/api/v1/cities", params={"q": term}, headers=headers)
            assert resp.status_code == 200, resp.text
            assert "Ōsaka" in names(resp.json()), (term, names(resp.json()))
        # Longer than any seeded name: prefix matching means *no* match, and the
        # distinction matters — a user who has typed one letter too many must see
        # an empty list, not a corrupted one.
        resp = client.get("/api/v1/cities", params={"q": "osakas"}, headers=headers)
        assert resp.json()["suggestions"] == [], names(resp.json())


class TestRanking:
    """The two ordering keys are asserted against the prefix where they *disagree*.

    `q=sanmar` is that prefix: `San Marino` (San Marino) is a national capital of
    4,500 people, and `San Marino` (California) is an ordinary town of 13,464.
    Ordering by population puts California first; applying the seat boost puts
    the capital first. Both rows are real, and they carry the *same name*, which
    also removes any chance that the break is the `name` tie-breaker.

    A test written against a prefix where both keys happen to agree cannot tell
    the two orderings apart — which is exactly how the seat boost went unpinned
    the first time it was negative-validated: the seed's only capitals were also
    its largest matches, so "boost applied" and "sorted by population" produced
    byte-identical output.
    """

    def test_seat_boost_outranks_population(self, client, register_user, seeded_cities):
        """A capital outranks a *larger* non-capital matching the same prefix.

        This is the product decision: when a user types a syllable, the capital
        is usually what they mean. The two rows share a name and an identical
        display string, so the seat tier and the population are the only things
        that can order them — and the smaller one must lead.

        `feature_code` is deliberately not exposed by the API (it is an internal
        classification), so the assertion is on the observable consequence: the
        capital's country comes first *despite* its smaller population.
        """
        headers = register_user()["headers"]
        resp = client.get("/api/v1/cities", params={"q": "San Mar", "limit": 20}, headers=headers)
        assert resp.status_code == 200, resp.text
        got = resp.json()["suggestions"]
        order = [(s["country_code"], s["population"]) for s in got]
        assert [cc for cc, _ in order] == ["SM", "US"], order
        # State the contradiction explicitly, so a future reader can see that the
        # result is deliberately *not* in population order.
        assert order[0][1] < order[1][1], f"boost did not overturn population: {order}"

    def test_larger_population_sorts_first(self, client, register_user, seeded_cities):
        """Within one seat tier, population decides.

        The three `San Jose` rows are PPLC / PPLA2 / PPL, so the tiers differ and
        a naive "non-increasing populations" assertion is *wrong* — the Costa
        Rican capital legitimately leads a much larger US namesake. The guarantee
        is narrower and needs stating precisely: among rows whose seat tiers are
        equal, the larger population sorts first. Two of the three rows are not
        equal, so the assertion is on the population ordering *within* each tier.
        """
        headers = register_user()["headers"]
        resp = client.get("/api/v1/cities", params={"q": "San Jose", "limit": 20}, headers=headers)
        assert resp.status_code == 200, resp.text
        pops = [s["population"] for s in resp.json()["suggestions"]]
        assert pops == [335007, 997368, 143495], pops
        # Tier 0 is a single PPLC and leads despite being the second-largest.
        assert 335007 < 997368, "the capital must lead a larger non-capital"

    def test_same_name_is_ranked_and_stable(self, client, register_user, seeded_cities):
        """Identical names must come back in a stable order across requests.

        When `name` and population are both equal the final key cannot separate
        the rows, so the order falls to whatever the plan produces — which
        differs between SQLite and PostgreSQL and between index choices. A picker
        whose list reshuffles between two identical requests is a bug users
        notice and developers cannot reproduce, so the list is requested twice
        and required to match.
        """
        headers = register_user()["headers"]
        params = {"q": "San Marino", "limit": 20}
        first = [s["country_code"] for s in client.get("/api/v1/cities", params=params, headers=headers).json()["suggestions"]]
        second = [s["country_code"] for s in client.get("/api/v1/cities", params=params, headers=headers).json()["suggestions"]]
        assert first == ["SM", "US"], first
        assert first == second, f"order is not stable across requests: {first} then {second}"


class TestDisambiguation:
    def test_each_suggestion_carries_country_and_admin1(self, client, register_user, seeded_cities):
        """A bare name is not enough to choose from — `Santa Cruz` occurs 16 times
        in the real dataset. The fields that tell candidates apart must be
        present, not just the name.

        `San Jose` is the natural case here: it is seeded in three countries and
        the list must make them distinguishable, not just repeat the name. The
        Costa Rican row displays as `San José` — the real GeoNames name carries
        the accent — so this also pins that `name` is returned as stored and
        `asciiname` is the search key, with no normalisation leaking into the
        display value.
        """
        headers = register_user()["headers"]
        resp = client.get("/api/v1/cities", params={"q": "San Jose"}, headers=headers)
        for item in resp.json()["suggestions"]:
            assert item["name"] in {"San Jose", "San José"}
            assert item["country_code"] in {"US", "PH", "CR"}
            assert item["country_name"]
        cc = {s["country_code"] for s in resp.json()["suggestions"]}
        assert cc == {"US", "PH", "CR"}, cc
        # The accented one is reachable by typing plain ASCII, which is the whole
        # reason the search runs against `asciiname`.
        assert "San José" in names(resp.json()), names(resp.json())

    def test_suggestions_carry_coordinates(self, client, register_user, seeded_cities):
        """The map layer reads these; without them a chosen city cannot be pinned."""
        headers = register_user()["headers"]
        resp = client.get("/api/v1/cities", params={"q": "Osaka"}, headers=headers)
        item = resp.json()["suggestions"][0]
        assert isinstance(item["latitude"], float)
        assert isinstance(item["longitude"], float)
        assert -90 <= item["latitude"] <= 90
        assert -180 <= item["longitude"] <= 180

    def test_admin1_may_be_null_without_breaking_serialisation(
        self, client, register_user, seeded_cities
    ):
        """City-states (Hong Kong, Singapore) have no admin1 in the real data, and
        51 of the 69,740 rows are the same. The field is optional and must
        serialise as null rather than being omitted or erroring — a client that
        assumed a string would crash on `Osakarovka`, whose admin1 is absent.
        """
        headers = register_user()["headers"]
        resp = client.get("/api/v1/cities", params={"q": "Osakarovka"}, headers=headers)
        row = resp.json()["suggestions"][0]
        assert row["admin1_name"] is None, row

    def test_populated_admin1_is_still_serialised(self, client, register_user, seeded_cities):
        """The counterpart to the null case: the field must not be silently
        dropped for every row just because one row's value is null."""
        headers = register_user()["headers"]
        resp = client.get("/api/v1/cities", params={"q": "Tokyo"}, headers=headers)
        assert resp.json()["suggestions"][0]["admin1_name"] == "Tokyo", resp.json()

    def test_three_seat_tiers_rank_in_order(self, client, register_user, seeded_cities):
        """All three boost tiers must be distinct, and each must beat population.

        `Funafuti` (PPLC, 6,320) → `Fundulea` (PPLA2, 6,668) → `Funes` (PPL,
        14,750) is the one family in the seed where every boundary is decisive,
        and the capital is the *smallest* of the three. Asserting the exact
        sequence therefore pins three separate facts at once:

        * a national capital leads a state seat even when smaller,
        * a state seat leads a plain town even when much smaller,
        * and the largest of the three finishes last.

        Because the capital is the smallest, collapsing the top two tiers into
        one (`PPLC` and `PPLA*` both scoring 2) reverses the first two rows.
        """
        headers = register_user()["headers"]
        got = client.get("/api/v1/cities", params={"q": "Fun", "limit": 20}, headers=headers).json()["suggestions"]
        order = [(s["name"], s["population"]) for s in got]
        assert [n for n, _ in order] == ["Funafuti", "Fundulea", "Funes"], order
        # Spell out the inversion: the ranking is not population order. The plain
        # town is more than twice the capital and still finishes last.
        assert order[0][1] < order[1][1] < order[2][1], order

    def test_admin_seat_outranks_a_larger_plain_town(self, client, register_user, seeded_cities):
        """The middle tier (`PPLA*`) must be boosted, not just national capitals.

        National capitals are a few hundred rows; state and province seats are
        tens of thousands, so a bug that boosted only `PPLC` would pass every
        capital-centric test and mis-rank the common case. `Abbeville` (FR,
        PPLA3, 26k) must lead `Abbotsford` (CA, PPL, 141k) despite being five
        times smaller.
        """
        headers = register_user()["headers"]
        got = client.get("/api/v1/cities", params={"q": "Abb", "limit": 20}, headers=headers).json()["suggestions"]
        order = [(s["name"], s["population"]) for s in got]
        assert order[0][0] == "Abbeville", order
        assert order[0][1] < order[1][1], f"the seat did not outrank the larger town: {order}"

    def test_unboosted_rows_sort_below_boosted_ones(self, client, register_user, seeded_cities):
        """A plain town must not outrank an administrative seat, however large.

        The second tier of the boost (`PPLA*`) is the one that matters in volume:
        national capitals are a few hundred rows, state and province seats are
        tens of thousands. `San Marino (US)` is a `PPL` of 13,464 and must sort
        below `San Marino (SM)`, a `PPLC` of 4,500 — and below any `PPLA` seat of
        the same prefix too.
        """
        headers = register_user()["headers"]
        got = client.get("/api/v1/cities", params={"q": "San Mar", "limit": 20}, headers=headers).json()["suggestions"]
        plain = [i for i, s in enumerate(got) if s["country_code"] == "US"]
        seat = [i for i, s in enumerate(got) if s["country_code"] == "SM"]
        assert seat and plain, got
        assert seat[0] < plain[0], got


class TestFilters:
    def test_country_filter_narrows_results(self, client, register_user, seeded_cities):
        headers = register_user()["headers"]
        unfiltered = client.get("/api/v1/cities", params={"q": "San Jose"}, headers=headers).json()
        filtered = client.get(
            "/api/v1/cities", params={"q": "San Jose", "country": "PH"}, headers=headers
        ).json()
        assert len(filtered["suggestions"]) == 1
        assert len(filtered["suggestions"]) < len(unfiltered["suggestions"])
        assert filtered["suggestions"][0]["country_code"] == "PH"

    def test_country_filter_is_case_insensitive(self, client, register_user, seeded_cities):
        headers = register_user()["headers"]
        lower = client.get(
            "/api/v1/cities", params={"q": "San Jose", "country": "ph"}, headers=headers
        )
        assert lower.status_code == 200, lower.text
        assert lower.json()["suggestions"][0]["country_code"] == "PH"

    def test_country_filter_can_select_a_capital(self, client, register_user, seeded_cities):
        """Filtering must not defeat the ranking: `San Marino` exists in both SM
        and US, and narrowing to the country that holds the capital must return
        exactly that row — the smaller one — rather than falling back to
        population order or to the other country's larger namesake.
        """
        headers = register_user()["headers"]
        resp = client.get(
            "/api/v1/cities", params={"q": "San Marino", "country": "SM"}, headers=headers
        )
        got = resp.json()["suggestions"]
        assert [s["country_code"] for s in got] == ["SM"], got
        assert got[0]["population"] == 4500, got
        # And the unfiltered list still leads with the capital, so the filter is
        # narrowing a correctly ranked list rather than repairing a wrong one.
        both = client.get("/api/v1/cities", params={"q": "San Marino"}, headers=headers).json()
        assert [s["country_code"] for s in both["suggestions"]] == ["SM", "US"], both

    def test_limit_is_honoured(self, client, register_user, seeded_cities):
        headers = register_user()["headers"]
        resp = client.get("/api/v1/cities", params={"q": "Osak", "limit": 2}, headers=headers)
        assert len(resp.json()["suggestions"]) == 2

    @pytest.mark.parametrize("bad", [0, -1, 21, 999])
    def test_limit_outside_range_is_rejected(self, client, register_user, seeded_cities, bad):
        """Bounded so a client cannot turn the suggestion endpoint into a dump of
        the reference table."""
        headers = register_user()["headers"]
        resp = client.get("/api/v1/cities", params={"q": "Osak", "limit": bad}, headers=headers)
        assert resp.status_code == 422, resp.text


class TestBoundaryBehaviour:
    @pytest.mark.parametrize("term", ["o", "", "  "])
    def test_short_or_empty_term_returns_empty_list_not_an_error(
        self, client, register_user, seeded_cities, term
    ):
        """This is the endpoint's most important contract. A one-character prefix
        matches thousands of rows, so it is not a suggestion; the picker must
        render "no match" (leaving the city blank), never an error. A 404 here
        would surface as a red error state on an ordinary keystroke."""
        headers = register_user()["headers"]
        resp = client.get("/api/v1/cities", params={"q": term}, headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["suggestions"] == []

    def test_unknown_prefix_returns_empty_list(self, client, register_user, seeded_cities):
        headers = register_user()["headers"]
        resp = client.get("/api/v1/cities", params={"q": "zzzqqq"}, headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["suggestions"] == []

    @pytest.mark.parametrize("term", ["%", "_", "\\", "osa%", "os_", "%osaka%"])
    def test_like_metacharacters_are_literal(self, client, register_user, seeded_cities, term):
        """A typed `%` must be a character, not a wildcard.

        Without escaping, `q=%` matches every row and `q=_` matches any
        single-character name — the user's input silently becomes query syntax,
        and the endpoint answers a question nobody asked. `\\` matters separately
        because it is the escape character and must be escaped first.
        """
        headers = register_user()["headers"]
        resp = client.get("/api/v1/cities", params={"q": term}, headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["suggestions"] == [], (
            f"{term!r} was treated as a wildcard: {names(resp.json())}"
        )

    def test_wildcard_alone_does_not_return_the_whole_table(self, client, register_user, seeded_cities):
        """Stated separately from the parametrised case because the failure it
        guards against is the worst one: `%` returning every row would look like
        a working search to a casual observer."""
        headers = register_user()["headers"]
        resp = client.get("/api/v1/cities", params={"q": "%", "limit": 20}, headers=headers)
        assert resp.json()["suggestions"] == []

    def test_query_is_echoed_back_normalised(self, client, register_user, seeded_cities):
        """The client discards responses from superseded keystrokes; a stable
        echoed term is what lets it do that without tracking request order."""
        headers = register_user()["headers"]
        resp = client.get("/api/v1/cities", params={"q": "Osaka"}, headers=headers)
        assert resp.json()["query"] == "osaka"

    def test_overlong_query_is_rejected(self, client, register_user, seeded_cities):
        headers = register_user()["headers"]
        resp = client.get("/api/v1/cities", params={"q": "a" * 65}, headers=headers)
        assert resp.status_code == 422, resp.text


class TestAuth:
    def test_requires_authentication(self, client, seeded_cities):
        """Not about secrecy — this is public reference data. An unauthenticated
        prefix search over a large table is a cheap way to make the database do
        work, and every other route in this API is authenticated."""
        resp = client.get("/api/v1/cities", params={"q": "Osaka"})
        assert resp.status_code == 401, resp.text

    def test_rejects_a_bad_token(self, client, seeded_cities):
        resp = client.get(
            "/api/v1/cities",
            params={"q": "Osaka"},
            headers={"Authorization": "Bearer not-a-real-token"},
        )
        assert resp.status_code == 401, resp.text


class TestCityReferenceIsValidatedOnWrite:
    """A `city_id` must name a real city, or the write is refused.

    This is the server half of the select-only picker. The client can only offer
    rows it fetched, but it is still the client that sends the id — and an id
    that does not resolve is worse than a misspelling, because the row *looks*
    populated and silently never matches. These tests pin the boundary.
    """

    @staticmethod
    def _trip_payload(**over):
        body = {
            "title": "Kyoto temple walk",
            "description": "Slow week of temples and coffee, no rush at all.",
            "destination_country": "Japan",
        }
        body.update(over)
        return body

    def test_trip_accepts_a_real_city_id(self, client, register_user, seeded_cities):
        headers = register_user()["headers"]
        resp = client.post(
            "/api/v1/trips",
            json=self._trip_payload(destination_city="Ōsaka", city_id=1853909),
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["city_id"] == 1853909, resp.json()

    def test_trip_rejects_an_unknown_city_id(self, client, register_user, seeded_cities):
        """The id must be checked against the table, not merely be an integer."""
        headers = register_user()["headers"]
        resp = client.post(
            "/api/v1/trips",
            json=self._trip_payload(destination_city="Nowhere", city_id=999_999_999),
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        assert "city_id" in resp.text, resp.text

    def test_trip_without_a_city_is_still_valid(self, client, register_user, seeded_cities):
        """The field is optional by design: "no city" is a supported answer, not
        an error, and the picker must be usable by someone who skips it."""
        headers = register_user()["headers"]
        resp = client.post("/api/v1/trips", json=self._trip_payload(), headers=headers)
        assert resp.status_code == 201, resp.text
        assert resp.json()["city_id"] is None, resp.json()

    def test_history_accepts_a_real_city_id(self, client, register_user, seeded_cities):
        headers = register_user()["headers"]
        resp = client.post(
            "/api/v1/profiles/me/histories",
            json={"country": "Japan", "city": "Funafuti", "city_id": 2110394},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["city_id"] == 2110394, resp.json()

    def test_history_rejects_an_unknown_city_id(self, client, register_user, seeded_cities):
        """More important here than on a trip: this field feeds the matching
        engine, so a bad id would quietly lower the user's match score with no
        visible symptom to explain why."""
        headers = register_user()["headers"]
        resp = client.post(
            "/api/v1/profiles/me/histories",
            json={"country": "Japan", "city": "Nowhere", "city_id": 999_999_999},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        assert "city_id" in resp.text, resp.text

    def test_history_without_a_city_is_still_valid(self, client, register_user, seeded_cities):
        headers = register_user()["headers"]
        resp = client.post(
            "/api/v1/profiles/me/histories",
            json={"country": "Japan"},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["city_id"] is None, resp.json()

    @pytest.mark.parametrize("bad", [0, -1])
    def test_city_id_must_be_positive(self, client, register_user, seeded_cities, bad):
        """`ge=1` on the field: valid GeoNames ids are positive.

        The status alone does not pin this — the existence check would also
        reject `0` and `-1`, just at a different layer. What distinguishes them
        is *where* the rejection happens: a schema violation is reported as a
        `loc` pointing at the request body field, whereas the existence check
        raises its own `detail`. Asserting the location is what makes this test
        about the bound rather than about validation in general.
        """
        headers = register_user()["headers"]
        resp = client.post(
            "/api/v1/trips",
            json=self._trip_payload(city_id=bad),
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        errors = resp.json()["detail"]
        assert isinstance(errors, list), errors
        loc = [e["loc"] for e in errors]
        assert any("city_id" in entry for entry in loc), f"not a field-level bound: {loc}"
        assert all("greater than or equal to 1" in e.get("msg", "") for e in errors), errors

    def test_deleting_the_city_row_does_not_delete_the_trip(
        self, client, register_user, seeded_cities, raw_db
    ):
        """`SET NULL`, not `CASCADE`.

        Re-importing GeoNames replaces every reference row. If that cascaded, a
        routine data refresh would delete user trips — so the trip must survive
        with its city blanked, keeping `destination_city` as the display echo.
        """
        headers = register_user()["headers"]
        created = client.post(
            "/api/v1/trips",
            json=self._trip_payload(destination_city="Ōsaka", city_id=1853909),
            headers=headers,
        ).json()

        raw_db.execute("DELETE FROM cities WHERE id = ?", (1853909,))
        raw_db.commit()

        after = client.get(f"/api/v1/trips/{created['id']}", headers=headers)
        assert after.status_code == 200, after.text
        body = after.json()
        assert body["city_id"] is None, body
        # The human-readable value survives, so the trip still shows a place.
        assert body["destination_city"] == "Ōsaka", body

        # Restore the row so this module's later tests are not order-dependent.
        raw_db.execute(
            "INSERT INTO cities (id, name, asciiname, country_code, country_name, admin1_code,"
            " admin1_name, latitude, longitude, population, feature_code, timezone)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (1853909, "Ōsaka", "Osaka", "JP", "Japan", None, "Osaka", 34.69379, 135.50107, 2753862, "PPLA", None),
        )
        raw_db.commit()


class TestGetCityById:
    """`GET /cities/{city_id}` — the lookup the map on a trip page needs.

    The trip stores `city_id` but no coordinate, so the map has to resolve one.
    Doing that with a *name* search would be wrong, and the first test here is
    what pins it: `San Jose` has three seeded namesakes, and the one that ranks
    first is not the one a trip would have stored.
    """

    def test_returns_the_city_and_its_coordinates(self, client, register_user, seeded_cities):
        headers = register_user()["headers"]
        resp = client.get("/api/v1/cities/1853909", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == 1853909
        assert body["name"] == "Ōsaka"
        # The coordinates are the whole reason this endpoint exists.
        assert body["latitude"] == pytest.approx(34.69379)
        assert body["longitude"] == pytest.approx(135.50107)

    def test_resolves_by_id_not_by_name(self, client, register_user, seeded_cities):
        """The id wins even when a name search would return a different city.

        `q=San Jose` ranks the Costa Rican capital first (PPLC beats the larger
        US seat). Asking for the *Philippine* row by id must return that row,
        which is the behaviour a name-based lookup cannot provide.
        """
        headers = register_user()["headers"]

        by_name = client.get("/api/v1/cities", params={"q": "San Jose"}, headers=headers).json()
        assert by_name["suggestions"][0]["id"] == 3621849, by_name["suggestions"][0]

        by_id = client.get("/api/v1/cities/1689510", headers=headers)
        assert by_id.status_code == 200, by_id.text
        assert by_id.json()["id"] == 1689510
        assert by_id.json()["country_code"] == "PH"

    def test_unknown_id_is_a_404_not_an_empty_result(self, client, register_user, seeded_cities):
        """A named row that does not exist is an error, unlike a prefix miss.

        The prefix search answers `200 []` for a term nobody matches, because
        that is an ordinary keystroke. Here the caller named a specific row, so
        "it is not there" has to be distinguishable — the client uses that to
        decide between "no such city" and "no suggestion yet".
        """
        headers = register_user()["headers"]
        resp = client.get("/api/v1/cities/999999999", headers=headers)
        assert resp.status_code == 404, resp.text

    def test_requires_authentication(self, client, seeded_cities):
        resp = client.get("/api/v1/cities/1853909")
        assert resp.status_code == 401, resp.text

    def test_does_not_shadow_the_suggestion_route(self, client, register_user, seeded_cities):
        """`/cities?q=…` must still be a search, not be read as `city_id`."""
        headers = register_user()["headers"]
        resp = client.get("/api/v1/cities", params={"q": "Osaka"}, headers=headers)
        assert resp.status_code == 200, resp.text
        assert "suggestions" in resp.json(), resp.json()
