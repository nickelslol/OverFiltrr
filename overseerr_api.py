import logging
from dataclasses import dataclass, field
from typing import Any, List, Optional

import requests


@dataclass
class Genre:
    id: int
    name: str

    @staticmethod
    def from_dict(data: dict) -> "Genre":
        return Genre(id=data.get("id"), name=data.get("name", ""))


@dataclass
class Keyword:
    id: int
    name: str

    @staticmethod
    def from_dict(data: dict) -> "Keyword":
        return Keyword(id=data.get("id"), name=data.get("name", ""))


@dataclass
class ProductionCompany:
    id: int
    name: str

    @staticmethod
    def from_dict(data: dict) -> "ProductionCompany":
        return ProductionCompany(id=data.get("id"), name=data.get("name", ""))


@dataclass
class MovieDetails:
    id: int
    imdbId: str
    genres: List[Genre] = field(default_factory=list)
    keywords: List[Keyword] = field(default_factory=list)
    releaseDate: Optional[str] = None
    watchProviders: Any = None
    productionCompanies: List[ProductionCompany] = field(default_factory=list)
    originalLanguage: str = ""
    status: str = ""
    overview: str = ""
    posterPath: str = ""
    releases: Any = None

    @staticmethod
    def from_dict(data: dict) -> "MovieDetails":
        keywords_data = data.get("keywords") or {}
        keyword_list = keywords_data.get("results", keywords_data) or []
        return MovieDetails(
            id=data.get("id"),
            imdbId=data.get("imdbId", ""),
            genres=[Genre.from_dict(g) for g in data.get("genres", [])],
            keywords=[Keyword.from_dict(k) for k in keyword_list],
            releaseDate=data.get("releaseDate"),
            watchProviders=data.get("watchProviders"),
            productionCompanies=[
                ProductionCompany.from_dict(pc)
                for pc in data.get("productionCompanies", [])
            ],
            originalLanguage=data.get("originalLanguage", ""),
            status=data.get("status", ""),
            overview=data.get("overview", ""),
            posterPath=data.get("posterPath", ""),
            releases=data.get("releases"),
        )


@dataclass
class TvDetails:
    id: int
    imdbId: str
    genres: List[Genre] = field(default_factory=list)
    keywords: List[Keyword] = field(default_factory=list)
    firstAirDate: Optional[str] = None
    watchProviders: Any = None
    productionCompanies: List[ProductionCompany] = field(default_factory=list)
    networks: List[ProductionCompany] = field(default_factory=list)
    originalLanguage: str = ""
    status: str = ""
    overview: str = ""
    posterPath: str = ""
    contentRatings: Any = None

    @staticmethod
    def from_dict(data: dict) -> "TvDetails":
        keywords_data = data.get("keywords") or {}
        keyword_list = keywords_data.get("results", keywords_data) or []
        return TvDetails(
            id=data.get("id"),
            imdbId=data.get("imdbId", ""),
            genres=[Genre.from_dict(g) for g in data.get("genres", [])],
            keywords=[Keyword.from_dict(k) for k in keyword_list],
            firstAirDate=data.get("firstAirDate"),
            watchProviders=data.get("watchProviders"),
            productionCompanies=[
                ProductionCompany.from_dict(pc)
                for pc in data.get("productionCompanies", [])
            ],
            networks=[ProductionCompany.from_dict(n) for n in data.get("networks", [])],
            originalLanguage=data.get("originalLanguage", ""),
            status=data.get("status", ""),
            overview=data.get("overview", ""),
            posterPath=data.get("posterPath", ""),
            contentRatings=data.get("contentRatings"),
        )


@dataclass
class MediaRequest:
    id: int
    status: int

    @staticmethod
    def from_dict(data: dict) -> "MediaRequest":
        return MediaRequest(id=data.get("id"), status=data.get("status"))


class OverseerrClient:
    """Simple client for Overseerr API endpoints used by OverFiltrr."""

    def __init__(
        self, base_url: str, api_key: str, session: Optional[requests.Session] = None
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/api/v1"
        self.api_key = api_key
        self.session = session or requests.Session()

    def _request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}{endpoint}"
        headers = kwargs.pop("headers", {})
        headers.setdefault("accept", "application/json")
        headers.setdefault("X-Api-Key", self.api_key)
        if method in {"post", "put", "patch"}:
            headers.setdefault("Content-Type", "application/json")
        try:
            response = self.session.request(
                method, url, headers=headers, timeout=5, **kwargs
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logging.error(
                "Overseerr API error during %s %s: %s", method.upper(), url, exc
            )
            raise
        return response

    def get_movie(self, movie_id: int) -> MovieDetails:
        resp = self._request("get", f"/movie/{movie_id}")
        return MovieDetails.from_dict(resp.json())

    def get_tv(self, tv_id: int) -> TvDetails:
        resp = self._request("get", f"/tv/{tv_id}")
        return TvDetails.from_dict(resp.json())

    def update_request(self, request_id: int, payload: dict) -> MediaRequest:
        resp = self._request("put", f"/request/{request_id}", json=payload)
        return MediaRequest.from_dict(resp.json())

    def approve_request(self, request_id: int) -> MediaRequest:
        resp = self._request("post", f"/request/{request_id}/approve")
        return MediaRequest.from_dict(resp.json())

    def get_request(self, request_id: int) -> MediaRequest:
        resp = self._request("get", f"/request/{request_id}")
        return MediaRequest.from_dict(resp.json())
