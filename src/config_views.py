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

__all__ = ["date_view", "reload_interval", "area_view", "ticket_number", "captcha_view", "answer_view", "play_sound_view"]


def date_view(config_dict):
    """The date stage's config bundle, as consumed by
    ``date_selection.select_date``.

    - ``enable``: ``date_auto_select.enable``; True when missing
      (the settings default). The stage's gating sites read this
      field (issue #60); ``select_date`` itself ignores it.
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
        "enable": date_select.get("enable", True),
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


def ticket_number(config_dict, default=2):
    """The count stage's ticket quantity (issue #56).

    ``ticket_number`` is a top-level scalar with no section, so this
    view is a scalar function like ``reload_interval``. The raw value
    is returned untouched -- call sites that coerce it (``str()`` /
    ``int()``) keep coercing. ``default`` is 2, the settings default;
    the few handlers that historically defaulted to 1 pass
    ``default=1`` explicitly so the divergence stays visible at the
    call site instead of being silently unified.
    """
    return config_dict.get("ticket_number", default)


def captcha_view(config_dict):
    """The captcha stage's config bundle (issue #58).

    - ``enable``: ``ocr_captcha.enable``; True when missing (the
      settings default). A few readers historically defaulted a
      missing key to False, silently disabling OCR; the view follows
      the settings default, as flagged in issue #58.
    - ``beta``: ``ocr_captcha.beta``; True when missing.
    - ``force_submit``: ``ocr_captcha.force_submit``; True when
      missing.
    - ``image_source``: ``ocr_captcha.image_source``; ``"canvas"``
      when missing or empty.

    The OCR factories in nodriver_common read ``use_universal`` and
    ``path`` themselves; those are factory-internal and deliberately
    not part of this view. A missing ``ocr_captcha`` section yields
    the defaults instead of raising (ADR 0004).
    """
    ocr = config_dict.get("ocr_captcha") or {}
    return {
        "enable": ocr.get("enable", True),
        "beta": ocr.get("beta", True),
        "force_submit": ocr.get("force_submit", True),
        "image_source": ocr.get("image_source") or "canvas",
    }


def answer_view(config_dict):
    """The verify-question stage's config bundle (issue #62).

    All three keys live in the ``advanced`` section:

    - ``auto_guess_options``: False when missing (the settings
      default, and what every current reader de-facto uses).
    - ``user_guess_string``: ``""`` when missing, returned raw --
      call sites that stripped the value keep stripping it.
    - ``discount_code``: ``""`` when missing, returned raw for the
      same reason.

    ``util.get_answer_list_from_user_guess_string`` reads
    ``user_guess_string`` itself and deliberately stays that way:
    it is the single choke point every platform calls, and util
    sits below this module in the dependency direction, so it
    cannot import the view.
    """
    advanced = config_dict.get("advanced") or {}
    return {
        "auto_guess_options": advanced.get("auto_guess_options", False),
        "user_guess_string": advanced.get("user_guess_string", ""),
        "discount_code": advanced.get("discount_code", ""),
    }


def play_sound_view(config_dict):
    """The sound gates' config bundle (issue #64).

    - ``ticket``: ``advanced.play_sound.ticket``; True when missing
      (the settings default).
    - ``order``: ``advanced.play_sound.order``; True when missing.

    Call sites keep deciding whether to call
    ``play_sound_while_ordering`` themselves; the view only replaces
    the gate read (no centralization, per the issue #64 consensus).
    The ``filename`` read inside ``play_sound_while_ordering`` is
    the callee's own business and stays there. Fansigo's
    platform-only ``advanced.play_ticket_sound`` key is not part of
    this view.
    """
    play_sound = (config_dict.get("advanced") or {}).get("play_sound") or {}
    return {
        "ticket": play_sound.get("ticket", True),
        "order": play_sound.get("order", True),
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
