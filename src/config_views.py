"""Per-stage config views over the raw config_dict (issue #30, ADR 0004).

Handlers used to index the raw ``config_dict`` directly -- hundreds of
``config_dict[...]`` / ``.get(...)`` sites with defaults re-derived (and
drifting) per platform. A *view* is a small pure function that answers
one pipeline stage's config questions in one place:

- Views are computed fresh from the dict on every call. The main loop
  mutates the dict in place between passes (``reload_config()``) so
  settings changed in the UI take effect without a restart; a view
  must never snapshot or cache.
- Defaults encode the de-facto behaviour of the current code, per key.
  Where platforms disagree on a default, the divergence is flagged and
  decided separately -- a view migration never silently unifies it.
- Views are cut per shared pipeline stage, not per platform. Platform
  sections and platform decisions stay in the platform modules.
"""

__all__ = ["date_view"]


def date_view(config_dict):
    """The date stage's config bundle, as consumed by
    ``date_selection.select_date``.

    - ``keyword``: ``date_auto_select.date_keyword``, stripped;
      ``""`` when missing or None.
    - ``mode``: ``date_auto_select.mode``; ``"from top to bottom"``
      when missing or empty.
    - ``keyword_exclude``: the raw top-level value, passed through
      untouched -- a missing key stays None, because consumers pin
      different behaviour for missing/None vs "" (see date_selection).
    - ``fallback``: ``date_auto_fallback``; False when missing.

    A missing ``date_auto_select`` section yields the defaults instead
    of raising (ADR 0004); in real runs the section always exists
    because ``get_config_dict`` migrates the loaded settings.
    """
    date_select = config_dict.get("date_auto_select") or {}
    return {
        "keyword": (date_select.get("date_keyword") or "").strip(),
        "mode": date_select.get("mode") or "from top to bottom",
        "keyword_exclude": config_dict.get("keyword_exclude"),
        "fallback": config_dict.get("date_auto_fallback", False),
    }
