"""City suggestion schemas.

The shape here is dictated by one constraint: **a bare city name is not enough
to choose from.** At `cities5000` granularity there are 69,740 rows and the same
name recurs — `Santa Cruz` appears 16 times, `Richmond` 15. A suggestion list
showing only `Santa Cruz` would be unusable, so every item carries the fields
needed to tell the candidates apart: country, first-level administrative
division, and population.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CityOut(BaseModel):
    """One selectable city."""

    model_config = ConfigDict(from_attributes=True)

    #: GeoNames `geonameid`. The value the client stores as `city_id`.
    id: int
    #: Display name, with diacritics (`Sant Julià de Lòria`).
    name: str
    #: ISO-3166 alpha-2 of the country/territory.
    country_code: str
    #: Display name of the country/territory (`Japan`).
    country_name: str
    #: First-level administrative division (`Osaka`, `California`). Null for
    #: city-states and for the handful of rows whose code does not resolve.
    admin1_name: str | None = None
    #: Ranked on this, and shown so the user can tell same-name cities apart.
    population: int
    #: Coordinates of the **city centre**. Present so the map layer can render
    #: without a second request. These never come from client input.
    latitude: float
    longitude: float


class CitySuggestionPage(BaseModel):
    """Suggestions for one search term.

    `suggestions` is a different word from `cities` on purpose: this is a
    bounded, ranked prefix match for a picker, not a paginated directory. A
    client that treats it as the latter will show 20 results and conclude the
    city is missing.
    """

    #: Echoed back so a client can discard responses from a superseded keystroke
    #: without tracking request order itself.
    query: str = Field(description="The normalised search term that was applied.")
    suggestions: list[CityOut]
