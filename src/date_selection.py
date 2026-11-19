"""Shared date-selection core (issue #22, ADR 0003).

Every platform's date stage runs the same pipeline -- collect rows,
drop unavailable and keyword-excluded ones, match the date keyword by
priority groups, fall back (or not) to a mode pick, click the winner --
but the keyword-priority algorithm used to be copy-pasted per platform.
This module is that pipeline, once, over plain data.

Boundary: the core knows nothing about the DOM. A platform adapter
extracts rows as dicts::

    {"text": str,          # row text used for keyword matching
     "available": bool,    # adapter-computed; sold-out signals differ
                           # per platform and cannot be unified here
     "payload": object}    # opaque to the core (element, index, node
                           # id, session dict); the adapter clicks it

and a config view::

    {"keyword": str,          # raw date_keyword string (JSON-ish)
     "mode": str,             # date_auto_select mode
     "keyword_exclude": ...,  # raw keyword_exclude value, passed
                              # through untouched (see select_date)
     "fallback": bool,        # date_auto_fallback
     "parse_error_all": bool} # optional; when True, a keyword parse
                              # or evaluation failure behaves like no
                              # keyword at all (kham's handler did this;
                              # default False = failure is a miss)

``select_date`` returns the index into ``rows`` of the chosen row, or
None. Reload / cooldown / return-value semantics stay in the handlers.

Semantics pinned by the kktix characterization tests
(``tests/test_kktix_date_auto_select.py``), the pilot adapter:

- Keyword groups are parsed from ``"[" + keyword + "]"`` as JSON and
  tried in order; the first group that matches any candidate row wins,
  and within a group the first matching row (in row order) wins. A
  string group is one substring; a list group requires all of its
  substrings (AND). Whitespace runs are collapsed on both sides before
  comparing. A parse failure behaves like no match, unless the config
  view sets ``parse_error_all`` (kham), in which case it behaves like
  no keyword. Group evaluation is lazy: a malformed group only fails
  the match when no earlier group matched first.
- With no keyword (or a keyword miss with fallback on), the mode pick
  over the candidate rows delegates to
  ``util.get_target_index_by_mode``; the exclude predicate delegates
  to ``util.reset_row_text_if_match_keyword_exclude``.

Note: ``util.get_matched_blocks_by_keyword`` is NOT reused for the
match itself -- it reads ``innerHTML`` off live elements and collects
every match of the winning group for a mode pick, which is a different
contract from the text-row, first-match-wins behaviour the platform
handlers actually implement. See ADR 0003.
"""

import json
import re

import util

_WHITESPACE_RUN = re.compile(r"\s+")


def _collapse_whitespace(text):
    return _WHITESPACE_RUN.sub(" ", text)


def _group_matches(group, normalized_text):
    if isinstance(group, list):
        return all(
            _collapse_whitespace(keyword) in normalized_text
            for keyword in group
        )
    if isinstance(group, str):
        return _collapse_whitespace(group) in normalized_text
    return False


def _priority_match(rows, candidate_indexes, keyword):
    """(index, failed): index of the first row matched by the first
    matching keyword group, or None. ``failed`` is True when parsing
    or evaluating a group raised; groups are evaluated lazily in
    priority order, so a failure only surfaces when no earlier group
    matched (the handlers' matching loops behaved the same way)."""
    try:
        keyword_groups = json.loads("[" + keyword + "]")
    except Exception:
        return None, True
    for group in keyword_groups:
        try:
            for index in candidate_indexes:
                normalized_text = _collapse_whitespace(rows[index]["text"])
                if _group_matches(group, normalized_text):
                    return index, False
        except Exception:
            return None, True
    return None, False


def select_date(rows, config_view):
    """Choose one row per the pipeline described in the module docstring.

    Returns the index into ``rows``, or None when nothing is chosen
    (no candidates, or a keyword miss with fallback off).
    """
    exclude_config = {"keyword_exclude": config_view.get("keyword_exclude")}
    candidate_indexes = []
    for index, row in enumerate(rows):
        if not row.get("available"):
            continue
        text = row.get("text") or ""
        # A row with empty text stays a candidate: it can still be
        # picked by mode/fallback, and it matches only an empty
        # keyword group. (Ticketmaster's two-pass extraction can
        # yield such a row when the text re-read fails; the exclude
        # predicate is not run on empty text, matching the handlers,
        # which only ever ran it on rows that had text.)
        if text:
            try:
                excluded = util.reset_row_text_if_match_keyword_exclude(
                    exclude_config, text
                )
            except Exception:
                # The handlers used to run this predicate inside their
                # per-row try, so a malformed keyword_exclude (missing,
                # None, non-string) skipped every row instead of raising.
                excluded = True
            if excluded:
                continue
        candidate_indexes.append(index)

    if not candidate_indexes:
        return None

    keyword = (config_view.get("keyword") or "").strip()
    if keyword:
        matched, match_failed = _priority_match(rows, candidate_indexes, keyword)
        if matched is not None:
            return matched
        if match_failed and config_view.get("parse_error_all"):
            # This platform's handler treated a keyword failure as
            # "no keyword" (its except set the matched list to every
            # row): fall through to the mode pick over all candidates,
            # regardless of the fallback switch.
            pass
        elif not config_view.get("fallback"):
            return None

    mode_index = util.get_target_index_by_mode(
        len(candidate_indexes), config_view.get("mode", "")
    )
    if mode_index is None:
        return None
    return candidate_indexes[mode_index]
