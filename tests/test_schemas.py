import pytest
from jsonschema import Draft202012Validator

from jobhunt.profile.importers.coursework import COURSEWORK_SCHEMA
from jobhunt.profile.importers.cv import CV_ATOM_SCHEMA
from jobhunt.profile.importers.github import REPO_ATOM_SCHEMA
from jobhunt.profile.interview import ATOM_SCHEMA

SCHEMAS_AND_EXAMPLES = [
    (
        "CV_ATOM_SCHEMA",
        CV_ATOM_SCHEMA,
        {
            "atoms": [
                {
                    "kind": "project",
                    "title": "Uploaded project",
                    "description": "A project description.",
                    "skills": ["Python", "FastAPI"],
                    "metrics": "100 req/s",
                }
            ]
        },
    ),
    (
        "REPO_ATOM_SCHEMA",
        REPO_ATOM_SCHEMA,
        {
            "kind": "project",
            "title": "URL shortener",
            "description": "Flask service backed by Redis.",
            "skills": ["Python", "Flask", "Redis"],
            "metrics": None,
        },
    ),
    (
        "COURSEWORK_SCHEMA",
        COURSEWORK_SCHEMA,
        {
            "atoms": [
                {
                    "title": "Distributed Systems",
                    "description": "Covered consensus, replication, and fault tolerance.",
                    "skills": ["Consensus", "Raft"],
                }
            ]
        },
    ),
    (
        "ATOM_SCHEMA",
        ATOM_SCHEMA,
        {
            "atoms": [
                {
                    "kind": "project",
                    "title": "Distributed cache",
                    "description": "Built distributed cache in Go.",
                    "skills": ["Go", "Distributed Systems"],
                    "metrics": "sub-millisecond latency",
                }
            ]
        },
    ),
]


@pytest.mark.parametrize("name,schema,example", SCHEMAS_AND_EXAMPLES)
def test_schema_is_valid_json_schema(name, schema, example):
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("name,schema,example", SCHEMAS_AND_EXAMPLES)
def test_schema_validates_representative_example(name, schema, example):
    validator = Draft202012Validator(schema)
    validator.validate(example)
