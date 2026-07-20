# Copyright © 2026 Province of British Columbia
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""Config for the fga-sync consumer.

Reuses the same DB, Pub/Sub, and OpenFGA env vars as auth-api so the two
services can share a .env file locally.
"""

import os

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())


def get_named_config(config_name: str = "production"):
    """Return the config object for the requested run mode."""
    if config_name in ("production", "staging", "default"):
        return ProdConfig()
    if config_name == "testing":
        return TestConfig()
    if config_name == "development":
        return DevConfig()
    raise KeyError(f"Unknown configuration: {config_name}")


class _Config:  # pylint: disable=too-few-public-methods
    """Base config."""

    PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    AUTH_LD_SDK_KEY = os.getenv("AUTH_LD_SDK_KEY")

    DB_USER = os.getenv("DATABASE_USERNAME", "")
    DB_PASSWORD = os.getenv("DATABASE_PASSWORD", "")
    DB_NAME = os.getenv("DATABASE_NAME", "")
    DB_HOST = os.getenv("DATABASE_HOST", "")
    DB_PORT = int(os.getenv("DATABASE_PORT", "5432"))
    DB_SCHEMA = os.getenv("DATABASE_SCHEMA", "public")
    SQLALCHEMY_DATABASE_URI = f"postgresql+pg8000://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

    OPENFGA_API_URL = os.getenv("OPENFGA_API_URL", "http://localhost:8081")
    OPENFGA_STORE_ID = os.getenv("OPENFGA_STORE_ID")
    OPENFGA_MODEL_ID = os.getenv("OPENFGA_MODEL_ID")
    OPENFGA_API_TOKEN = os.getenv("OPENFGA_API_TOKEN")


class DevConfig(_Config):  # pylint: disable=too-few-public-methods
    """Dev config."""

    DEBUG = True
    TESTING = False


class TestConfig(_Config):  # pylint: disable=too-few-public-methods
    """Test config."""

    DEBUG = True
    TESTING = True


class ProdConfig(_Config):  # pylint: disable=too-few-public-methods
    """Prod config."""

    DEBUG = False
    TESTING = False
