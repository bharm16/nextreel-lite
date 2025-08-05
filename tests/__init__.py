# -*- coding: utf-8 -*-

"""
__init__.py
~~~~~~~~~~~

This test suite checks the methods of tmdbsimple.

Use the following command to run all the tests:
    python -W ignore:ResourceWarning -m unittest discover tests

:copyright: (c) 2013-2022 by Celia Oakley.
:license: GPLv3, see LICENSE for more details.
"""

import os

"""Utilities for tests to access required credentials.

Credentials are loaded from environment variables to avoid storing secrets in
the repository. Tests should handle missing values gracefully.
"""

API_KEY = os.getenv("TMDB_API_KEY")
USERNAME = os.getenv("TMDB_USERNAME")
PASSWORD = os.getenv("TMDB_PASSWORD")
SESSION_ID = os.getenv("TMDB_SESSION_ID")

__all__ = ["API_KEY", "USERNAME", "PASSWORD", "SESSION_ID"]
