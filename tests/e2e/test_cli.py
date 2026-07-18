from __future__ import annotations

import argparse

from alpha_foundry.cli import _parser


def test_e2e_cli_exposes_only_article_add_and_alpha_generate() -> None:
    parser = _parser()
    subparsers = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )

    assert set(subparsers.choices) == {"article-add", "alpha-generate"}
