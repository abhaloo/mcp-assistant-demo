"""Typed Cube data model and its YAML rendering."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

CubeDimensionType = Literal["string", "number", "time", "boolean"]
CubeMeasureType = Literal["count", "sum", "avg", "min", "max", "count_distinct"]
CubeRelationship = Literal["one_to_many", "many_to_one"]


class CubeDimension(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    name: str
    sql: str
    type: CubeDimensionType
    primary_key: bool = False


class CubeMeasure(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    name: str
    type: CubeMeasureType
    sql: str
    filters: tuple[str, ...] = ()


class CubeJoin(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    name: str
    relationship: CubeRelationship
    sql: str


class CubeDef(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    name: str
    sql: str
    dimensions: tuple[CubeDimension, ...]
    measures: tuple[CubeMeasure, ...]
    joins: tuple[CubeJoin, ...]
    # Columns the relation SQL already restricts (entity scope, record predicates) and the
    # column the runtime rewrite restricts per request. Together they are the forced
    # predicates this cube enforces without the adapter translating anything.
    relation_columns: tuple[str, ...] = ()
    department_column: str | None = None

    def as_document(self) -> dict:
        cube: dict = {
            "name": self.name,
            "sql": self.sql,
            "dimensions": [
                {
                    "name": d.name,
                    "sql": d.sql,
                    "type": d.type,
                    **({"primary_key": True} if d.primary_key else {}),
                }
                for d in self.dimensions
            ],
            "measures": [
                {
                    "name": m.name,
                    "type": m.type,
                    "sql": m.sql,
                    **({"filters": [{"sql": f} for f in m.filters]} if m.filters else {}),
                }
                for m in self.measures
            ],
        }
        if self.joins:
            cube["joins"] = [
                {"name": j.name, "relationship": j.relationship, "sql": j.sql} for j in self.joins
            ]
        return {"cubes": [cube]}


class CubeMemberMap(BaseModel):
    """Plan member names and view member names, both directions, built from the model."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    plan_to_view: dict[str, str]
    view_to_plan: dict[str, str]

    def knows(self, plan_member: str) -> bool:
        return plan_member in self.plan_to_view

    def to_view(self, plan_member: str) -> str:
        return self.plan_to_view[plan_member]

    def to_plan(self, view_member: str) -> str:
        return self.view_to_plan[view_member]


def revision_for(bundle_hash: str, generator_version: str) -> str:
    """Identity of a generated model: the bundle it came from and the generator that made it."""
    return hashlib.sha256(f"{bundle_hash}:{generator_version}".encode()).hexdigest()


class CubeModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    cubes: tuple[CubeDef, ...]
    view_name: str
    bundle_hash: str
    generator_version: str

    @property
    def revision(self) -> str:
        return revision_for(self.bundle_hash, self.generator_version)

    def yaml_documents(self) -> dict[str, str]:
        """One YAML file per cube plus the view, keys in declaration order, so a
        regenerated model diffs cleanly. The view carries the revision so readiness can
        verify which model Cube serves."""
        documents = {
            f"{cube.name}.yml": yaml.safe_dump(
                cube.as_document(), sort_keys=False, allow_unicode=True
            )
            for cube in self.cubes
        }
        view = {
            "views": [
                {
                    "name": self.view_name,
                    "meta": {"model_revision": self.revision},
                    "cubes": [
                        {"join_path": cube.name, "includes": "*", "prefix": True}
                        for cube in self.cubes
                    ],
                }
            ]
        }
        documents[f"{self.view_name}.yml"] = yaml.safe_dump(
            view, sort_keys=False, allow_unicode=True
        )
        return documents

    def _view_member(self, cube: CubeDef, member: str) -> str:
        return f"{self.view_name}.{cube.name}_{member}"

    def scope_document(self) -> str:
        """What the runtime rewrite needs: every view member of a department-scoped cube
        mapped to that cube's department member. Read by deploy/cube/cube.py."""
        department_members: dict[str, str] = {}
        for cube in self.cubes:
            if cube.department_column is None:
                continue
            target = self._view_member(cube, cube.department_column)
            for name in [d.name for d in cube.dimensions] + [m.name for m in cube.measures]:
                department_members[self._view_member(cube, name)] = target
        payload = {"view": self.view_name, "department_members": department_members}
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"

    def forced_columns(self, cube_name: str) -> frozenset[str]:
        """Columns a forced predicate may name on this cube and still be enforced."""
        for cube in self.cubes:
            if cube.name == cube_name:
                extra = {cube.department_column} if cube.department_column else set()
                return frozenset(cube.relation_columns) | frozenset(extra)
        return frozenset()

    def member_map(self) -> CubeMemberMap:
        """A dimension `resource.name` is `view.resource_name`; a measure `name` owned by
        `resource` is `view.resource_name`. Measures keep their bare plan name. Two plan
        members may never share a view member (the generator guarantees it; asserted here
        so a future bundle cannot silently alias two columns)."""
        plan_to_view: dict[str, str] = {}
        for cube in self.cubes:
            for dimension in cube.dimensions:
                plan_to_view[f"{cube.name}.{dimension.name}"] = self._view_member(
                    cube, dimension.name
                )
            for measure in cube.measures:
                plan_to_view[measure.name] = self._view_member(cube, measure.name)
        view_to_plan = {v: k for k, v in plan_to_view.items()}
        if len(view_to_plan) != len(plan_to_view):
            raise ValueError("generated view members are not unique")
        return CubeMemberMap(plan_to_view=plan_to_view, view_to_plan=view_to_plan)
