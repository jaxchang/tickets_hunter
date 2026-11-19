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

``reload_interval`` is the one view whose answer is deliberately not
deterministic: with the range setting on, each call draws a fresh
value inside the configured range. That draw is the setting's own
semantics (it used to live in ``util.get_reload_interval``), not
caching or hidden state.
"""

import random

__all__ = ["date_view", "reload_interval", "area_view"]


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


def area_view(config_dict):
    """The area stage's config bundle (issue #54).

    - ``enable``: ``area_auto_select.enable``; True when missing
      (the settings default).
    - ``mode``: ``area_auto_select.mode``; ``"random"`` when missing
      or empty (the settings default, CONST_SELECT_ORDER_DEFAULT).
    - ``keyword``: ``area_auto_select.area_keyword``, returned raw --
      call sites that stripped the value keep stripping it, because
      the current readers disagree (indexing readers strip, ``.get``
      readers do not).
    - ``keyword_exclude``: the top-level value; ``""`` when missing.
      Area readers de-facto default to "" here, unlike the date
      stage's raw pass-through in date_view.
    - ``fallback``: ``area_auto_fallback``; False when missing.

    A missing ``area_auto_select`` section yields the defaults instead
    of raising (ADR 0004); in real runs the section always exists
    because ``get_config_dict`` migrates the loaded settings.
    """
    area_select = config_dict.get("area_auto_select") or {}
    return {
        "enable": area_select.get("enable", True),
        "mode": area_select.get("mode") or "random",
        "keyword": area_select.get("area_keyword", ""),
        "keyword_exclude": config_dict.get("keyword_exclude", ""),
        "fallback": config_dict.get("area_auto_fallback", False),
    }


def reload_interval(config_dict, default=0):
    """Return the seconds to wait before the next auto reload.

    With the range setting on, auto_reload_page_interval is the lower
    bound and auto_reload_page_interval_max the upper bound, and each
    call draws a new value. Anything that is not a valid range returns
    the interval as is, so 0 still means reload disabled and a
    hand-edited value behaves as before.

    Args:
        config_dict: Configuration dictionary
        default: Value used when auto_reload_page_interval is missing

    Returns:
        The wait in seconds, or the configured value unchanged
    """
    advanced = config_dict.get("advanced", {})
    interval = advanced.get("auto_reload_page_interval", default)
    if not advanced.get("auto_reload_page_interval_range", False):
        return interval

    upper = advanced.get("auto_reload_page_interval_max", 0)
    for value in (interval, upper):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return interval
    if not 0 < interval < upper:
        return interval

    # Clamp because rounding can step outside a very narrow range.
    return max(interval, min(upper, round(random.uniform(interval, upper), 2)))
