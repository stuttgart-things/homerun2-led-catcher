"""The committed OpenAPI spec is the one the routers produce (#98)."""

from __future__ import annotations

from pathlib import Path

import yaml

from led_catcher.openapi import render

SPEC = Path(__file__).resolve().parent.parent / "docs" / "openapi.yaml"


def spec() -> dict:
    return yaml.safe_load(SPEC.read_text())


def test_the_committed_spec_is_current():
    assert SPEC.read_text() == render(), "docs/openapi.yaml is stale — run `task openapi` and commit the result"


def test_it_documents_the_machine_facing_api_and_nothing_else():
    assert set(spec()["paths"]) == {"/display", "/display/options", "/streams", "/healthz"}


def test_writes_need_the_token_and_reads_do_not():
    paths = spec()["paths"]
    assert paths["/display"]["post"]["security"] == [{"bearerAuth": []}]
    assert paths["/display"]["delete"]["security"] == [{"bearerAuth": []}]
    assert "security" not in paths["/display"]["get"]
    assert "security" not in paths["/display/options"]["get"]
    assert spec()["components"]["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"


def test_write_errors_are_documented():
    post = spec()["paths"]["/display"]["post"]["responses"]
    assert {"400", "401", "404", "422", "429"} <= set(post)


def test_rule_matching_fields_are_not_part_of_the_request():
    props = spec()["components"]["schemas"]["DisplayRequest"]["properties"]
    assert set(props) == {"kind", "text", "image", "font", "color", "duration", "hold"}


# ---- catalog-info.yaml points at it ------------------------------------------------

CATALOG = SPEC.parent.parent / "catalog-info.yaml"


def test_the_catalog_api_entity_points_at_the_spec():
    docs = list(yaml.safe_load_all(CATALOG.read_text()))
    component = next(d for d in docs if d["kind"] == "Component")
    api = next(d for d in docs if d["kind"] == "API")

    assert api["metadata"]["name"] in component["spec"]["providesApis"]
    assert api["spec"]["type"] == "openapi"
    target = CATALOG.parent / api["spec"]["definition"]["$text"]
    assert target.resolve() == SPEC.resolve()
