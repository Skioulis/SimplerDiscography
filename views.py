"""HTTP routes for SimpleDiscography."""

from __future__ import annotations

from flask import Blueprint, render_template

from stats import dashboard_stats

main = Blueprint("main", __name__)


@main.app_template_filter("gr")
def group_number(value):
    """Format an integer with '.' thousands separators (Greek convention)."""
    try:
        return f"{int(value):,}".replace(",", ".")
    except (TypeError, ValueError):
        return value


@main.route("/")
def dashboard():
    return render_template("dashboard.html", stats=dashboard_stats())
